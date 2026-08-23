# LLM 客户端：OpenAI 兼容 chat/completions（默认 DeepSeek），stdlib urllib 实现。
# 零新依赖；配置解析优先级（高→低）：
#   1. 显式传参（HttpLLM(api_key=...) / llm_config(override=...)）
#   2. SQLite data/govjob.db 的 llm_config 表（H5「LLM 设置」页保存；root 给定时生效）
#   3. 环境变量 DEEPSEEK_API_KEY / LLM_API_KEY（后者优先级高，为部署机约定）
#      + LLM_BASE_URL（默认 https://api.deepseek.com）+ LLM_MODEL（默认 deepseek-chat）
# 未配置 key → llm_available() 为 False，C 类源跳过（健康记录如实说明）。
# 测试注入 FakeLLM：实现 chat_json(system, user) -> dict 即可。
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Protocol

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class LLMClient(Protocol):
    def chat_json(self, system: str, user: str) -> dict: ...


def _db_config(root=None, db=None) -> dict:
    """读 SQLite 里的 LLM 配置；库不存在/无记录/坏库一律空 dict（不拖垮调用方）。"""
    if root is None and db is None:
        return {}
    try:
        from app.store.db import db_file, get_llm_config

        path = Path(db) if db else db_file(Path(root))
        row = get_llm_config(path)
        if row and row.get("api_key"):
            return {
                "api_key": row["api_key"],
                "base_url": row.get("base_url") or "",
                "model": row.get("model") or "",
            }
    except Exception:
        pass
    return {}


def llm_config(root=None, db=None) -> dict:
    """合并 SQLite（若 root/db 给定且已配置）与环境变量；显式 DB 值优先。"""
    cfg = _db_config(root, db)
    env_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY") or ""
    return {
        "api_key": cfg.get("api_key") or env_key,
        "base_url": cfg.get("base_url") or (os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        "model": cfg.get("model") or os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
    }


def llm_available(root=None, db=None) -> bool:
    return bool(llm_config(root, db)["api_key"])


class HttpLLM:
    """OpenAI 兼容 /chat/completions 调用（DeepSeek 等）。失败重试 1 次。"""

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "", timeout: int = 90, root=None, db=None):
        cfg = llm_config(root, db)
        self.api_key = api_key or cfg["api_key"]
        self.base_url = base_url or cfg["base_url"]
        self.model = model or cfg["model"]
        self.timeout = timeout

    def chat_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": 4000,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                content = data["choices"][0]["message"]["content"]
                return parse_json_loose(content)
            except Exception as e:  # 网络/HTTP/解析错误统一重试一次
                last_err = e
                if attempt == 0:
                    time.sleep(2)
        raise RuntimeError(f"LLM 调用失败: {last_err}")


def test_llm_connection(api_key: str, base_url: str, timeout: int = 15) -> dict:
    """轻量连通性验证：GET {base_url}/models（OpenAI 兼容端点，不耗 token）。
    返回 {ok, detail}；HTTP 401/403 = key 无效，其他错误原样带出。"""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        n = len(data.get("data") or [])
        return {"ok": True, "detail": f"连接成功，服务端返回 {n} 个可用模型"}
    except urllib.error.HTTPError as e:
        hint = {401: "API Key 无效或已过期", 403: "无权限（Key 被禁用或额度受限）"}.get(e.code, f"HTTP {e.code}")
        return {"ok": False, "detail": f"{hint}"}
    except Exception as e:
        return {"ok": False, "detail": f"连接失败: {e}"}


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_json_loose(text: str) -> dict:
    """容错解析：优先整体 json.loads，失败则抓首个 {...} 块（剥 markdown 围栏）。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {"data": obj}
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if not m:
            raise ValueError(f"LLM 输出中找不到 JSON: {text[:200]}")
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {"data": obj}


class FakeLLM:
    """测试替身：按公告文本关键词回放预制 JSON。"""

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def chat_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        return self.response
