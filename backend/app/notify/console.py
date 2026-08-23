from __future__ import annotations

from app.notify.base import Notifier


class ConsoleNotifier(Notifier):
    """本地调试渠道：打印到控制台。"""

    name = "console"

    def send(self, title: str, markdown: str) -> None:
        print(f"===== {title} =====")
        print(markdown)
