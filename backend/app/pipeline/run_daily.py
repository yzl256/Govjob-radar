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
        for src in sources:
            try:
                if src.tier == "A" and src.extractor in ("excel", "excel_or_llm"):
                    ok, detail, files = harvest_source(src, inbox)
                    health.append(HealthRecord(src.id, ok, detail, fetched_items=len(files)))
                elif src.extractor == "llm" and src.is_active_source:
                    if llm is None:
                        health.append(
                            HealthRecord(
                                src.id, False, "未配置 LLM（DEEPSEEK_API_KEY/LLM_API_KEY），跳过 C 类源"
                            )
                        )
                        continue
                    from app.pipeline.c_extract import harvest_c_source

                    ok, detail, new_jobs = harvest_c_source(src, llm, root, catalogs)
                    health.append(HealthRecord(src.id, ok, detail, fetched_items=new_jobs))
                else:
                    continue
            except Exception as e:
                health.append(HealthRecord(src.id, False, f"{type(e).__name__}: {e}"))
            print(f"[health] {src.id}: {'OK' if health[-1].ok else 'FAIL'} {health[-1].detail}")
    health_path = root / "data" / "source_health.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(
        json.dumps(
            [
                {
                    "source_id": h.source_id,
                    "ok": h.ok,
                    "detail": h.detail,
                    "fetched_items": h.fetched_items,
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                for h in health
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # ── 解析 + 匹配 + 推送 ─────────────────────────────────
    from app.pipeline.daily import filter_active_jobs
    from app.store.jobs import load_jobs

    stored = load_jobs(root)
    inbox_jobs, bad = collect_jobs_from_inbox(inbox, catalogs)
    jobs = inbox_jobs + stored
    expired = len([j for j in jobs if j.apply_deadline and j.apply_deadline < today])
    jobs = filter_active_jobs(jobs, today)
    print(
        f"[daily] inbox 解析: {len(inbox_jobs)} 岗位（{bad} 个文件无法解析）；"
        f"岗位库: {len(stored)} 条；过滤过期: {expired} 条 → 参与匹配 {len(jobs)} 条"
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
