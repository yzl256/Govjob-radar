# 模型供应商注册表：Base URL 封装在后端，前端只选供应商+贴 Key+选模型。
# 全部为 OpenAI 兼容 /chat/completions 端点（HttpLLM 零改动可用）。
# 新增供应商：在此加一项即可，前端供应商列表自动出现。
from __future__ import annotations

PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "moonshot": {
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
        "key_url": "https://platform.moonshot.cn/console/api-keys",
    },
    "zhipu": {
        "name": "智谱 AI",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-air", "glm-4-flash"],
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    "qwen": {
        "name": "阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo"],
        "key_url": "https://bailian.console.aliyun.com/?apiKey=1",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "key_url": "https://platform.openai.com/api-keys",
    },
}


def provider_view(pid: str) -> dict | None:
    """对外视图：不含 base_url（后端封装，前端无感）。"""
    p = PROVIDERS.get(pid)
    if not p:
        return None
    return {"id": pid, "name": p["name"], "models": p["models"], "key_url": p["key_url"]}
