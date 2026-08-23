# Server酱（Turbo）适配器：消息直达微信。
# 使用：https://sct.ftqq.com/ 扫码获取 SendKey → .env: SERVERCHAN_SENDKEY
# 免费额度每日 5 条，个人日报场景足够。
from __future__ import annotations

import json
import os
import urllib.request

from app.notify.base import Notifier

_API = "https://sctapi.ftqq.com/{key}.send"


class ServerChanNotifier(Notifier):
    name = "serverchan"

    def __init__(self, sendkey: str | None = None):
        self.sendkey = sendkey or os.environ.get("SERVERCHAN_SENDKEY", "")

    def send(self, title: str, markdown: str) -> None:
        if not self.sendkey:
            raise RuntimeError("未配置 SERVERCHAN_SENDKEY")
        data = json.dumps({"title": title[:32], "desp": markdown}).encode()
        req = urllib.request.Request(
            _API.format(key=self.sendkey),
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            if body.get("code") != 0:
                raise RuntimeError(f"Server酱返回异常: {body}")
