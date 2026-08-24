# 零依赖 Web 服务（stdlib http.server + pydantic 校验）
# 环境装不了 FastAPI（见 README），接口保持薄，真实仓库可平移。
from __future__ import annotations

import json
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


def _get_jobs(state: _State):
    inbox = state.root / "data" / "inbox"
    jobs_store = state.root / "data" / "jobs.jsonl"
    mtimes = [f.stat().st_mtime for f in inbox.glob("*.xlsx")]
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
        if route == "/api/meta":
            provinces = [
                f.stem
                for f in sorted((st.root / "config" / "sources").glob("*.yaml"))
                if not f.stem.startswith("_") and f.stem != "national"
            ]
            self._send(200, {"provinces": provinces, "paths": _PATHS})
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
        if self.path == "/api/profile":
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
        if self.path == "/api/llm":
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
        if self.path == "/api/llm/verify":
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
        if self.path == "/api/llm/test":
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
        if self.path == "/api/match":
            profile = _load_profile(st)
            jobs, bad = _get_jobs(st)
            from datetime import date as _date

            from app.knowledge.cycles import track_board
            from app.pipeline.daily import build_report, split_by_deadline

            today = _date.today()
            active, expired = split_by_deadline(jobs, today)
            report = build_report(profile, active, st.matcher, today=today)  # 现在可报名投递
            self._send(
                200,
                {
                    "name": profile.name,
                    "counts": {
                        "eligible": len(report.eligible),
                        "insufficient": len(report.insufficient),
                        "ineligible": report.ineligible_count,
                        "jobs_total": len(active),
                        "bad_files": bad,
                        "archived_expired": len(expired),  # 已截止存档数（不参与展示）
                    },
                    "eligible": [_serialize_result(x) for x in report.eligible],
                    "insufficient": [_serialize_result(x) for x in report.insufficient],
                    "top_fail_reasons": report.top_fail_reasons,
                    "track_board": track_board(profile, today),
                },
            )
            return
        self._send(404, {"error": "not found"})


def run(root: Path, port: int = 8420, open_browser: bool = True) -> None:
    Handler.state = _State(Path(root))
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
    print(f"[web] {url}  (Ctrl+C 停止)")
    httpd.serve_forever()
