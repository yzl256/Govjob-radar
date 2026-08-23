# SQLite 持久层（stdlib sqlite3，零新依赖）：LLM 配置 + 用户档案。
# 设计要点：
#  - 库文件 data/govjob.db（data/ 为运行时数据区，永不进版本库）
#  - llm_config 列式存三字段；user_profile 存 JSON 文档（UserProfile 含嵌套
#    education 数组，pydantic model_dump/validate 往返已有既定路径）
#  - ThreadingHTTPServer 多线程访问 → 每操作短连接 + WAL + busy_timeout，
#    单用户本地工具足够（部署机换 PostgreSQL 接口不变）
#  - API key 存明文（本地私有库文件，权限依赖文件系统）；对外展示一律 mask_key
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_config (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    api_key    TEXT NOT NULL DEFAULT '',
    base_url   TEXT NOT NULL DEFAULT '',
    model      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_profile (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def db_file(root: Path, path: Optional[Path] = None) -> Path:
    return Path(path) if path else Path(root) / "data" / "govjob.db"


def connect(db: Path) -> sqlite3.Connection:
    """短连接：建库建表（幂等）→ WAL → 忙等 3s。用完即 close。"""
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=3.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── LLM 配置 ─────────────────────────────────────────────
def get_llm_config(db: Path) -> Optional[dict]:
    """无记录返回 None；有记录返回 {api_key, base_url, model, updated_at}。"""
    with closing(connect(db)) as conn:
        row = conn.execute("SELECT * FROM llm_config WHERE id=1").fetchone()
    if row is None:
        return None
    return dict(row)


def save_llm_config(
    db: Path,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """部分更新 upsert：None/未传字段保留原值（前端"留空不改"语义）。
    返回更新后的完整配置。"""
    with closing(connect(db)) as conn:
        row = conn.execute("SELECT * FROM llm_config WHERE id=1").fetchone()
        cur = {"api_key": "", "base_url": "", "model": ""}
        if row is not None:
            cur = {k: row[k] for k in cur}
        if api_key is not None:
            cur["api_key"] = api_key.strip()
        if base_url is not None:
            cur["base_url"] = base_url.strip().rstrip("/")
        if model is not None:
            cur["model"] = model.strip()
        conn.execute(
            "INSERT INTO llm_config (id, api_key, base_url, model, updated_at)"
            " VALUES (1, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET api_key=excluded.api_key,"
            " base_url=excluded.base_url, model=excluded.model,"
            " updated_at=excluded.updated_at",
            (cur["api_key"], cur["base_url"], cur["model"], _now()),
        )
        conn.commit()
    return cur


def mask_key(key: str) -> str:
    """sk-AbCdEf123456 → sk-A****3456（首 4 + **** + 尾 4，过短全打码）。"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:4]}****{key[-4:]}"


# ── 用户档案 ─────────────────────────────────────────────
def get_profile(db: Path) -> Optional[dict]:
    """返回档案 dict（UserProfile model_dump 结果）；无记录返回 None。"""
    with closing(connect(db)) as conn:
        row = conn.execute("SELECT data FROM user_profile WHERE id=1").fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["data"])
    except (json.JSONDecodeError, TypeError):
        return None


def save_profile(db: Path, profile: dict) -> None:
    """整份覆盖保存（前端每次提交完整档案）。"""
    with closing(connect(db)) as conn:
        conn.execute(
            "INSERT INTO user_profile (id, data, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET data=excluded.data,"
            " updated_at=excluded.updated_at",
            (json.dumps(profile, ensure_ascii=False), _now()),
        )
        conn.commit()
