# 零依赖 Web 服务（stdlib http.server + pydantic 校验）
# 环境装不了 FastAPI（见 README），接口保持薄，真实仓库可平移。
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from app.knowledge.alias import load_aliases
from app.knowledge.catalogs import load_catalogs
from app.matching.engine import Matcher
from app.models.profile import UserProfile
from app.pipeline.run_daily import collect_jobs_from_inbox

_PATHS = {1: "选调生", 2: "国考", 3: "省考", 4: "事业单位", 5: "人才引进", 6: "国企央企",
          7: "军队文职", 8: "三支一扶", 9: "特岗/西部计划", 10: "辅导员/社区工作者"}

# 31 个省级行政区（民政部序）：订阅列表全量可选，不限于已注册源站的省份
_ALL_PROVINCES = (
    "北京市", "天津市", "河北省", "山西省", "内蒙古自治区", "辽宁省", "吉林省", "黑龙江省",
    "上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省", "河南省",
    "湖北省", "湖南省", "广东省", "广西壮族自治区", "海南省", "重庆市", "四川省", "贵州省",
    "云南省", "西藏自治区", "陕西省", "甘肃省", "青海省", "宁夏回族自治区", "新疆维吾尔自治区",
)
_MAINTENANCE_INTERVAL_SECONDS = 6 * 60 * 60


def _province_of_text(*texts) -> Optional[str]:
    """从岗位的地区/单位文本识别省级行政区：全称优先、短名兜底（如 浙江省/浙江）。"""
    blob = "".join(t or "" for t in texts)
    for full in _ALL_PROVINCES:
        if full in blob:
            return full
    for full in _ALL_PROVINCES:
        short = full[:2]
        if short in blob:
            return full
    return None


class _State:
    def __init__(self, root: Path, profile_file: Optional[Path] = None, db_path: Optional[Path] = None):
        from app.store.db import db_file

        self.root = Path(root)
        self.profile_file = Path(profile_file) if profile_file else self.root / "config" / "profiles" / "user.json"
        self.db = db_file(self.root, db_path)  # SQLite：LLM 配置 + 用户档案
        self.web_dir = root / "web"
        self.catalogs = load_catalogs(root)
        self.aliases = load_aliases(root)
        self.matcher = Matcher(self.catalogs, self.aliases)
        self._jobs_cache = None
        self._jobs_mtime = None
        self.sync_lock = threading.Lock()
        self.sync_status_lock = threading.Lock()
        self.sync_status = {"state": "idle", "provinces": [], "completed": 0, "total": 0, "sources": []}
        self.maintenance_stop = threading.Event()
        self.maintenance_status_lock = threading.Lock()
        self.maintenance_status = {"state": "idle", "last_run": None, "error": None}


def _invalidate_jobs_cache(state: _State) -> None:
    state._jobs_cache = None
    state._jobs_mtime = None


def _run_maintenance_once(state: _State, review_sources: bool = False) -> None:
    """执行一次维护并记录状态；失败绝不阻塞 H5 服务。"""
    from datetime import datetime

    try:
        from app.pipeline.maintenance import maintain_job_store

        summary = maintain_job_store(state.root, review_sources=review_sources)
        if summary.archive.total:
            _invalidate_jobs_cache(state)
        with state.maintenance_status_lock:
            state.maintenance_status = {
                "state": "ok",
                "last_run": datetime.now().isoformat(timespec="seconds"),
                "error": None,
                "archived_expired": summary.archive.archived_expired,
                "archived_result_publications": summary.archive.archived_result_publications,
                "reviewed_sources": summary.reviewed_sources,
            }
    except Exception as exc:
        with state.maintenance_status_lock:
            state.maintenance_status = {
                "state": "error",
                "last_run": datetime.now().isoformat(timespec="seconds"),
                "error": f"{type(exc).__name__}: {exc}",
            }


def _start_maintenance_loop(state: _State) -> None:
    """8420 自带的低频维护：每 6 小时复核少量来源并归档确定失效岗位。"""
    def worker():
        while not state.maintenance_stop.is_set():
            # 同步写库期间不重写 jobs.jsonl；下一轮会补跑，避免并发丢记录。
            if state.sync_lock.acquire(blocking=False):
                try:
                    _run_maintenance_once(state, review_sources=True)
                finally:
                    state.sync_lock.release()
            if state.maintenance_stop.wait(_MAINTENANCE_INTERVAL_SECONDS):
                break

    threading.Thread(target=worker, daemon=True, name="govjob-maintenance").start()


def _load_profile(state: _State) -> UserProfile:
    """SQLite 为权威源；首次运行把既有 config/profiles/user.json 迁移入库。"""
    from app.store.db import get_profile, save_profile

    stored = get_profile(state.db)
    if stored is not None:
        try:
            return UserProfile.model_validate(stored)
        except Exception:
            pass  # 库里旧数据不合法 → 走文件/默认，下次保存覆盖修复
    if state.profile_file.exists():
        profile = UserProfile.model_validate_json(
            state.profile_file.read_text(encoding="utf-8")
        )
        try:
            save_profile(state.db, profile.model_dump(mode="json"))
        except Exception:
            pass  # 迁移失败不阻塞读取
        return profile
    return UserProfile(name="user", education=[])


def _start_background_sync(state: _State, provinces: list[str]) -> bool:
    """启动单个同步任务；状态仅供 H5 轮询展示真实来源完成进度。"""
    if not state.sync_lock.acquire(blocking=False):
        return False

    from app.scheduler.sources import load_sources

    total = len([s for s in load_sources(state.root, provinces) if s.province_file in set(provinces)])
    with state.sync_status_lock:
        state.sync_status = {
            "state": "running", "provinces": provinces, "completed": 0,
            "total": total, "sources": [], "error": None,
        }

    def worker():
        try:
            from app.pipeline.run_daily import sync_subscribed_sources

            def progress(record, completed, source_total):
                with state.sync_status_lock:
                    state.sync_status["sources"].append(record)
                    state.sync_status["completed"] = completed
                    state.sync_status["total"] = source_total

            sources = sync_subscribed_sources(state.root, provinces, on_progress=progress)
            _invalidate_jobs_cache(state)
            with state.sync_status_lock:
                state.sync_status.update({"state": "done", "completed": len(sources), "sources": sources})
        except Exception as e:
            with state.sync_status_lock:
                state.sync_status.update({"state": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            state.sync_lock.release()

    threading.Thread(target=worker, daemon=True, name="govjob-sync").start()
    return True


def _get_jobs(state: _State):
    inbox = state.root / "data" / "inbox"
    jobs_store = state.root / "data" / "jobs.jsonl"
    # 目录 mtime 同样纳入缓存键：新增、删除职位表都会改变目录本身，
    # 否则删掉样例 xlsx 后旧缓存仍会把它显示在结果中。
    mtimes = [inbox.stat().st_mtime] + [f.stat().st_mtime for f in inbox.glob("*.xlsx")]
    if jobs_store.exists():
        mtimes.append(jobs_store.stat().st_mtime)
    mtime = max(mtimes, default=0.0)
    if state._jobs_cache is None or mtime != state._jobs_mtime:
        from app.store.jobs import load_jobs

        jobs, bad = collect_jobs_from_inbox(inbox, state.catalogs)
        jobs = jobs + load_jobs(state.root)
        state._jobs_cache = (jobs, bad)
        state._jobs_mtime = mtime
    return state._jobs_cache


def _serialize_result(jr) -> dict:
    job, res = jr.job, jr.result
    return {
        "id": job.id,
        "path": job.path,
        "path_name": _PATHS.get(job.path, "?"),
        "title": job.title,
        "employer": job.employer,
        "region": job.region_detail,
        "quota": job.quota,
        "deadline": job.apply_deadline.isoformat() if job.apply_deadline else None,
        "source_url": job.source_url,
        "verdict": res.verdict.value,
        "reasons": [r.model_dump() for r in res.reasons],
        "attention": res.attention,
        "details": _job_details(job),
        "is_sample": job.id.startswith("inbox-sample-") or "sample_" in (job.source_url or ""),
    }


def _job_details(job) -> dict:
    """详情字段只透出公告已记录的内容；空值由前端显示为“公告未披露”。"""
    return {
        "responsibilities": job.responsibilities,
        "compensation": job.compensation or job.highlights,
        "application_url": job.application_url,
        "application_process": job.application_process,
        "other_notes": job.other_notes,
        "verification_status": job.verification_status,
        "verification_note": job.verification_note,
        "edu": job.edu_require.model_dump(mode="json"),
        "major_rules": [x.model_dump(mode="json") for x in job.major_rules],
    }


def _serialize_recommendation(job, tier: str, reason: str) -> dict:
    return {
        "id": job.id, "path": job.path, "path_name": _PATHS.get(job.path, "国企央企"),
        "title": job.title, "employer": job.employer, "region": job.region_detail,
        "quota": job.quota, "deadline": job.apply_deadline.isoformat() if job.apply_deadline else None,
        "source_url": job.source_url, "tier": tier, "match_reason": reason,
        "details": _job_details(job),
        "is_sample": job.id.startswith("inbox-sample-") or "sample_" in (job.source_url or ""),
    }


def _llm_view(cfg: dict, masked: bool = True) -> dict:
    """对外安全视图：api_key 只出脱敏形式；base_url 不出（后端按供应商封装）。"""
    from app.llm.providers import PROVIDERS
    from app.store.db import mask_key

    pid = cfg.get("provider") or "deepseek"
    p = PROVIDERS.get(pid) or {}
    return {
        "configured": bool((cfg.get("api_key") or "").strip()),
        "provider": pid,
        "provider_name": p.get("name", pid),
        "api_key_masked": mask_key(cfg.get("api_key") or ""),
        "model": cfg.get("model") or "",
    }


class Handler(BaseHTTPRequestHandler):
    state: _State = None  # 注入

    def log_message(self, fmt, *args):
        pass  # 安静模式

    # ── helpers ──────────────────────────────────────────
    def _send(self, code: int, payload, content_type="application/json; charset=utf-8"):
        body = (
            json.dumps(payload, ensure_ascii=False).encode()
            if not isinstance(payload, (bytes, str))
            else (payload.encode() if isinstance(payload, str) else payload)
        )
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")  # 页面/接口迭代后浏览器不残留旧版
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ── routes ───────────────────────────────────────────
    def do_GET(self):
        st = self.state
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            index = st.web_dir / "index.html"
            if index.exists():
                self._send(200, index.read_text(encoding="utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, "web/index.html 不存在")
            return
        if route == "/api/profile":
            self._send(200, _load_profile(st).model_dump(mode="json"))
            return
        if route == "/api/sync/status":
            with st.sync_status_lock:
                self._send(200, dict(st.sync_status))
            return
        if route == "/api/maintenance/status":
            with st.maintenance_status_lock:
                self._send(200, dict(st.maintenance_status))
            return
        if route == "/api/meta":
            sources_ready = [
                f.stem
                for f in sorted((st.root / "config" / "sources").glob("*.yaml"))
                if not f.stem.startswith("_") and f.stem != "national"
            ]
            # 全省份可选：已接源省份按官方序排前（前端打 🟢 标），未接源的也可订阅（接入 yaml 后生效）
            known = [p for p in _ALL_PROVINCES if p in sources_ready]
            extra = [s for s in sources_ready if s not in _ALL_PROVINCES]  # 非省级行政区命名的源（如市级）
            rest = [p for p in _ALL_PROVINCES if p not in sources_ready]
            self._send(200, {"provinces": known + extra + rest, "sources_ready": sources_ready, "paths": _PATHS})
            return
        if route == "/api/llm":
            from app.store.db import get_llm_config

            cfg = get_llm_config(st.db) or {}
            view = _llm_view(cfg)
            if not view["configured"]:
                # 库里没有 → 看环境变量是否兜底（供前端提示"env 已配置"）
                from app.llm.client import llm_config
                from app.store.db import mask_key

                env = llm_config()  # 不传 root：纯环境变量
                if env["api_key"]:
                    view = {
                        "configured": True,
                        "provider": "deepseek",
                        "provider_name": "DeepSeek 深度求索",
                        "api_key_masked": mask_key(env["api_key"]) + "（环境变量）",
                        "model": env["model"],
                    }
            self._send(200, view)
            return
        if route == "/api/llm/providers":
            from app.llm.providers import PROVIDERS

            self._send(
                200,
                {
                    "providers": [
                        {"id": pid, "name": p["name"], "models": p["models"], "key_url": p["key_url"]}
                        for pid, p in PROVIDERS.items()
                    ]
                },
            )
            return
        if route == "/api/dfc":
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(self.path).query)
            school = (qs.get("school") or [""])[0].strip()
            if not school:
                self._send(400, {"error": "缺少 school 参数"})
                return
            hit = st.matcher.schools.lookup(school)
            self._send(
                200,
                {
                    "query": school,
                    "double_first_class": hit is not None,
                    "matched": hit,  # 命中的官方校名；null=未知（≠不是）
                },
            )
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        st = self.state
        route = self.path.split("?", 1)[0]
        if route == "/api/profile":
            try:
                profile = UserProfile.model_validate(self._body())
            except Exception as e:
                self._send(400, {"error": f"档案格式无效: {e}"})
                return
            from app.store.db import save_profile

            try:
                save_profile(st.db, profile.model_dump(mode="json"))  # 权威源：SQLite
            except Exception as e:
                self._send(500, {"error": f"档案写入数据库失败: {e}"})
                return
            try:  # 镜像写回 JSON：CLI match / daily 流水线仍读 config/profiles/*.json
                st.profile_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = st.profile_file.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(st.profile_file)
            except Exception:
                pass  # 镜像失败不影响主流程（SQLite 已落库）
            self._send(200, {"ok": True})
            return
        if route == "/api/llm":
            from app.llm.providers import PROVIDERS
            from app.store.db import save_llm_config

            body = self._body()
            pid = (body.get("provider") or "").strip()
            if pid not in PROVIDERS:
                self._send(400, {"error": f"未知供应商: {pid or '(空)'}"})
                return
            try:
                # base_url 永远由后端按供应商解析，前端不传不显示
                saved = save_llm_config(
                    st.db,
                    provider=pid,
                    api_key=(body.get("api_key") or "").strip() or None,  # 空=保留原 key（仅换模型）
                    base_url=PROVIDERS[pid]["base_url"],
                    model=(body.get("model") or "").strip() or None,
                )
            except Exception as e:
                self._send(500, {"error": f"配置写入数据库失败: {e}"})
                return
            self._send(200, {"ok": True, "saved": _llm_view(saved)})
            return
        if route == "/api/llm/verify":
            from app.llm.client import test_llm_connection
            from app.llm.providers import PROVIDERS

            body = self._body()
            pid = (body.get("provider") or "").strip()
            key = (body.get("api_key") or "").strip()
            if pid not in PROVIDERS:
                self._send(400, {"error": f"未知供应商: {pid or '(空)'}"})
                return
            if not key:
                self._send(400, {"error": "请先填入 API Key"})
                return
            model = (body.get("model") or "").strip() or PROVIDERS[pid]["models"][0]
            result = test_llm_connection(key, PROVIDERS[pid]["base_url"], model=model)
            result["provider_name"] = PROVIDERS[pid]["name"]
            self._send(200, result)  # 验证失败是业务结果而非协议错误，前端按 ok 分支
            return
        if route == "/api/llm/test":
            from app.llm.client import test_llm_connection
            from app.llm.providers import PROVIDERS
            from app.store.db import get_llm_config

            cfg = get_llm_config(st.db) or {}
            key = (cfg.get("api_key") or "").strip()
            if not key:
                self._send(400, {"error": "尚未保存 API Key，请先保存再测试"})
                return
            pid = cfg.get("provider") or "deepseek"
            base = cfg.get("base_url") or (PROVIDERS.get(pid) or {}).get("base_url") or "https://api.deepseek.com"
            result = test_llm_connection(key, base, model=cfg.get("model") or "")
            self._send(200, result)
            return
        if route == "/api/sync/start":
            profile = _load_profile(st)
            provinces = profile.subscribed_provinces or []
            if not provinces:
                self._send(400, {"error": "请先在档案中至少订阅一个省份，再同步岗位"})
                return
            if not _start_background_sync(st, provinces):
                self._send(409, {"error": "已有同步任务正在执行，请等待完成"})
                return
            self._send(202, {"ok": True, "provinces": provinces})
            return
        if route == "/api/sync":
            profile = _load_profile(st)
            provinces = profile.subscribed_provinces or []
            if not provinces:
                self._send(400, {"error": "请先在档案中至少订阅一个省份，再同步岗位"})
                return
            if not st.sync_lock.acquire(blocking=False):
                self._send(409, {"error": "已有同步或数据维护任务正在执行，请稍后再试"})
                return
            try:
                from app.pipeline.run_daily import sync_subscribed_sources

                sources = sync_subscribed_sources(st.root, provinces)
                # 岗位库或 inbox 可能在同步中发生变化；下一次 match 必须重新解析。
                _invalidate_jobs_cache(st)
                self._send(200, {"ok": True, "provinces": provinces, "sources": sources})
            except Exception as e:
                self._send(500, {"error": f"同步岗位失败: {type(e).__name__}: {e}"})
            finally:
                st.sync_lock.release()
            return
        if route == "/api/match":
            profile = _load_profile(st)
            jobs, bad = _get_jobs(st)
            from datetime import date as _date

            from app.knowledge.cycles import track_board
            from app.matching.recommend import recommend_soe, split_recommendations
            from app.pipeline.daily import build_report
            from app.pipeline.source_review import invalid_source_urls
            from app.store.jobs import archive_summary
            from app.validity import split_displayable_jobs

            today = _date.today()
            # 展示层兜底：即便维护线程尚未归档，过期和名单公示也绝不进入任何
            # 匹配分支、推荐分支或省份统计。
            active, expired, result_publications = split_displayable_jobs(
                jobs, today=today, invalid_source_urls=invalid_source_urls(st.root)
            )
            with_deadline = [j for j in active if j.apply_deadline is not None]
            without_deadline = [j for j in active if j.apply_deadline is None]

            # 无截止日不代表失效，但也没有“仍在招”的证据：不进入优先投递，
            # 仅作为“值得核验/长期招聘”候选出现。
            strict = [j for j in with_deadline if j.path != 6]
            verified = [j for j in strict if j.verification_status == "verified"]
            pending = [j for j in strict if j.verification_status != "verified"]
            unknown_deadline_strict = [j for j in without_deadline if j.path != 6]
            report = build_report(profile, verified, st.matcher, today=today)
            soe = split_recommendations([j for j in with_deadline if j.path == 6], profile)
            for job in (j for j in without_deadline if j.path == 6):
                level, _reason = recommend_soe(job, profile)
                if level or job.verification_status == "pending":
                    soe["pending"].append((job, "公告未提供明确报名截止日，请先核验是否仍在招"))

            # 在招岗位按省份分布：订阅省份无岗位时前端明示原因，不再静默
            province_counts: dict = {}
            sample_province_counts: dict = {}
            for j in with_deadline:
                prov = _province_of_text(j.region_detail, j.employer, j.title)
                if prov:
                    if j.id.startswith("inbox-sample-") or "sample_" in (j.source_url or ""):
                        sample_province_counts[prov] = sample_province_counts.get(prov, 0) + 1
                    else:
                        province_counts[prov] = province_counts.get(prov, 0) + 1
            archived = archive_summary(st.root)
            self._send(
                200,
                {
                    "name": profile.name,
                    "counts": {
                        "eligible": len(report.eligible),
                        "insufficient": len(report.insufficient),
                        "ineligible": report.ineligible_count,
                        "jobs_total": len(with_deadline),
                        "unverified_no_deadline": len(without_deadline),
                        "bad_files": bad,
                        # 保持旧字段语义：无论记录仍在等待归档，还是已迁入归档区，
                        # 用户都能看到累计已隐藏/归档数量。
                        "archived_expired": archived.archived_expired + len(expired),
                        "archived_invalid": (
                            archived.archived_result_publications + len(result_publications)
                        ),
                        "archived_total": (
                            archived.total + len(expired) + len(result_publications)
                        ),
                        "hidden": {
                            "expired": len(expired),
                            "invalid_result_publication": len(result_publications),
                        },
                    },
                    "eligible": [_serialize_result(x) for x in report.eligible],
                    "insufficient": [_serialize_result(x) for x in report.insufficient],
                    "strict": {
                        "eligible": [_serialize_result(x) for x in report.eligible],
                        "insufficient": [_serialize_result(x) for x in report.insufficient],
                        "pending": [
                            _serialize_recommendation(
                                j, "pending", j.verification_note or "尚未取得岗位表或关键资格字段"
                            ) for j in pending
                        ] + [
                            _serialize_recommendation(
                                j, "pending", "公告未提供明确报名截止日，请先核验是否仍在招"
                            ) for j in unknown_deadline_strict
                        ],
                    },
                    "soe": {k: [_serialize_recommendation(j, k, reason) for j, reason in rows] for k, rows in soe.items()},
                    "top_fail_reasons": report.top_fail_reasons,
                    "province_counts": province_counts,
                    "sample_province_counts": sample_province_counts,
                    "store_total": len(jobs),
                    "track_board": track_board(profile, today),
                },
            )
            return
        self._send(404, {"error": "not found"})


def run(root: Path, port: int = 8420, open_browser: bool = True) -> None:
    Handler.state = _State(Path(root))
    # 启动时先做无外网依赖的本地归档；来源页复核转入后台线程，确保 8420
    # 不会因个别官网变慢而无法打开。
    _run_maintenance_once(Handler.state, review_sources=False)
    _start_maintenance_loop(Handler.state)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        import threading
        import webbrowser

        t = threading.Timer(0.8, webbrowser.open, args=(url,))  # 等端口就绪再开页
        t.daemon = True
        t.start()
    try:  # Windows GBK 控制台遇到 emoji/宽字符时降级替换而不是崩栈
        import sys

        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"[web] {url}  (Ctrl+C 停止；岗位库每 6 小时自动维护)")
    try:
        httpd.serve_forever()
    finally:
        Handler.state.maintenance_stop.set()
        httpd.server_close()
