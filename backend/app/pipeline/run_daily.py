# 单趟每日流水线：抓取(best-effort) → 解析 inbox → 匹配 → 推送
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

from app.crawler.guokao import parse_guokao_workbook
from app.crawler.fetch import harvest_source
from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import load_catalogs
from app.matching.engine import Matcher
from app.models.job import Job
from app.models.profile import UserProfile
from app.pipeline.daily import build_report
from app.scheduler.sources import HealthRecord, load_profiles, load_sources


def collect_jobs_from_inbox(inbox: Path, catalogs) -> Tuple[List[Job], int]:
    """解析 inbox 里所有 xlsx（通用职位表解析器；非职位表文件跳过不报错）。
    id 前缀取文件名哈希，避免多文件间 gk-00001 撞车。"""
    import hashlib
    import re

    jobs: List[Job] = []
    bad = 0
    for f in sorted(inbox.glob("*.xlsx")):
        try:
            stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "-", f.stem)[:16]
            h = hashlib.sha1(f.name.encode()).hexdigest()[:6]
            parsed = parse_guokao_workbook(
                str(f), catalogs,
                source_url=f"file://{f.name}",
                job_prefix=f"inbox-{stem}-{h}",
            )
            if parsed:
                jobs.extend(parsed)
            else:
                bad += 1
        except Exception:
            bad += 1
    return jobs, bad


def _health_view(health: List[HealthRecord]) -> List[dict]:
    return [
        {
            "source_id": h.source_id,
            "ok": h.ok,
            "detail": h.detail,
            "fetched_items": h.fetched_items,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for h in health
    ]


def sync_subscribed_sources(root: Path, provinces: List[str], llm=None, on_progress=None) -> List[dict]:
    """同步指定订阅省份的源站，供 H5 的「立即匹配」在匹配前调用。

    不带全国源，避免用户仅同步浙江时意外下载或调用无关源站。返回每个源的
    可展示健康结果；B 类 structured 平台尚未实现时也明确反馈，不再静默跳过。
    """
    root = Path(root)
    catalogs = load_catalogs(root)
    sources = [s for s in load_sources(root, provinces) if s.province_file in set(provinces)]
    if llm is None:
        from app.llm.client import HttpLLM, llm_available

        if llm_available(root=root):
            llm = HttpLLM(root=root)
    health: List[HealthRecord] = []
    inbox = root / "data" / "inbox"
    for src in sources:
        try:
            if src.tier == "A" and src.extractor in ("excel", "excel_or_llm") and src.is_active_source:
                ok, detail, files = harvest_source(src, inbox)
                health.append(HealthRecord(src.id, ok, detail, fetched_items=len(files)))
            elif src.extractor in ("llm", "excel_or_llm") and src.is_active_source:
                if llm is None:
                    health.append(HealthRecord(src.id, False, "未配置大模型，无法抽取公告", 0))
                else:
                    from app.pipeline.c_extract import harvest_c_source

                    ok, detail, new_jobs = harvest_c_source(src, llm, root, catalogs)
                    health.append(HealthRecord(src.id, ok, detail, fetched_items=new_jobs))
            elif src.extractor == "structured":
                health.append(HealthRecord(src.id, False, "该平台的结构化抓取尚未接入", 0))
            else:
                health.append(HealthRecord(src.id, False, "源站未配置可用入口", 0))
        except Exception as e:
            health.append(HealthRecord(src.id, False, f"{type(e).__name__}: {e}", 0))
        print(f"[health] {src.id}: {'OK' if health[-1].ok else 'FAIL'} {health[-1].detail}")
        if on_progress is not None:
            # H5 实时展示真实来源结果；回调异常绝不能影响抓取主流程。
            try:
                on_progress(_health_view([health[-1]])[0], len(health), len(sources))
            except Exception:
                pass

    health_path = root / "data" / "source_health.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(json.dumps(_health_view(health), ensure_ascii=False, indent=2), encoding="utf-8")
    # 同步写入完成后立即执行一次纯本地归档；不等待额外外网复核，避免拉长
    # 用户点击“同步岗位”的时间。结果公示在 C 类抓取阶段会直接写入来源复核缓存。
    from app.pipeline.maintenance import maintain_job_store

    cleaned = maintain_job_store(root)
    if cleaned.archive.total:
        print(
            f"[maintenance] 已归档：截止 {cleaned.archive.archived_expired} 条，"
            f"结果公示 {cleaned.archive.archived_result_publications} 条"
        )
    return _health_view(health)


def run_daily(
    root: Path,
    do_fetch: bool = True,
    today: date = None,
    notifier=None,
    llm=None,
) -> Dict[str, dict]:
    """一次完整流水线。返回 {profile_name: {report, notified}} + health 摘要打在 stdout。
    llm：LLM 客户端（有 chat_json 方法）。为 None 时自动探测环境变量配置；未配置则跳过 C 类源。"""
    today = today or date.today()
    root = Path(root)
    catalogs = load_catalogs(root)
    aliases = load_aliases(root)
    matcher = Matcher(catalogs, aliases)

    if llm is None and do_fetch:
        from app.llm.client import HttpLLM, llm_available

        if llm_available(root=root):  # SQLite（H5 配置页）优先，环境变量兜底
            llm = HttpLLM(root=root)

    profiles_raw = load_profiles(root)
    profiles = []
    for raw in profiles_raw:
        try:
            profiles.append(UserProfile.model_validate(raw))
        except Exception as e:
            print(f"[daily] 档案无效，跳过: {raw.get('name', '?')} ({e})")
    if not profiles:
        print("[daily] 无有效档案")
        return {}

    subscribed = {p for prof in profiles for p in (prof.subscribed_provinces or [])}
    sources = load_sources(root, sorted(subscribed))
    inbox = root / "data" / "inbox"
    out_dir = root / "data" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 抓取（best-effort，失败不影响本地匹配）──────────────
    health: List[HealthRecord] = []
    if do_fetch:
        # 每日任务包含全国源；H5 的即时同步只处理订阅省份，见 sync_subscribed_sources。
        for src in sources:
            if src.province_file == "national":
                # 保持既有“全国源恒启用”的调度口径。
                try:
                    if src.tier == "A" and src.extractor in ("excel", "excel_or_llm") and src.is_active_source:
                        ok, detail, files = harvest_source(src, inbox)
                        health.append(HealthRecord(src.id, ok, detail, fetched_items=len(files)))
                    elif src.extractor in ("llm", "excel_or_llm") and src.is_active_source and llm is not None:
                        from app.pipeline.c_extract import harvest_c_source
                        ok, detail, new_jobs = harvest_c_source(src, llm, root, catalogs)
                        health.append(HealthRecord(src.id, ok, detail, fetched_items=new_jobs))
                    elif src.extractor == "structured":
                        health.append(HealthRecord(src.id, False, "该平台的结构化抓取尚未接入", 0))
                except Exception as e:
                    health.append(HealthRecord(src.id, False, f"{type(e).__name__}: {e}"))
                if health:
                    print(f"[health] {health[-1].source_id}: {'OK' if health[-1].ok else 'FAIL'} {health[-1].detail}")
        # 省级源与 H5 同步复用同一实现，避免两条流程再次漂移。
        province_health = sync_subscribed_sources(root, sorted(subscribed), llm=llm)
        health.extend(HealthRecord(x["source_id"], x["ok"], x["detail"], x["fetched_items"]) for x in province_health)
    health_path = root / "data" / "source_health.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(
        json.dumps(
            _health_view(health),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # ── 解析 + 匹配 + 推送 ─────────────────────────────────
    from app.pipeline.maintenance import maintain_job_store
    from app.pipeline.source_review import invalid_source_urls
    from app.store.jobs import load_jobs
    from app.validity import split_displayable_jobs

    cleaned = maintain_job_store(root, today=today)
    if cleaned.archive.total:
        print(
            f"[maintenance] 已归档：截止 {cleaned.archive.archived_expired} 条，"
            f"结果公示 {cleaned.archive.archived_result_publications} 条"
        )
    stored = load_jobs(root)
    inbox_jobs, bad = collect_jobs_from_inbox(inbox, catalogs)
    jobs = inbox_jobs + stored
    jobs, expired, result_publications = split_displayable_jobs(
        jobs, today=today, invalid_source_urls=invalid_source_urls(root)
    )
    print(
        f"[daily] inbox 解析: {len(inbox_jobs)} 岗位（{bad} 个文件无法解析）；"
        f"岗位库: {len(stored)} 条；隐藏截止: {len(expired)} 条；"
        f"隐藏结果公示: {len(result_publications)} 条 → 参与匹配 {len(jobs)} 条"
    )

    results: Dict[str, dict] = {}
    for prof in profiles:
        report = build_report(prof, jobs, matcher, today=today)
        text = report.render_text()
        out_file = out_dir / f"daily_{today.isoformat()}_{prof.name or 'user'}.txt"
        out_file.write_text(text, encoding="utf-8")

        notified = None
        if notifier is not None:
            try:
                notifier.send(f"{prof.name or '你'}的岗位日报 {today.isoformat()}", text)
                notified = "ok"
            except Exception as e:
                print(f"[notify] 推送失败，已落盘 {out_file}: {e}")
                notified = f"fail:{e}"
        results[prof.name or "user"] = {
            "report_file": str(out_file),
            "eligible": len(report.eligible),
            "insufficient": len(report.insufficient),
            "ineligible": report.ineligible_count,
            "notified": notified,
        }
    return results
