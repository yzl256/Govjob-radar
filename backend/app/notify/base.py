from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    """推送渠道适配器。send() 抛异常由调度层捕获降级，不中断流水线。"""

    name: str = "base"

    @abstractmethod
    def send(self, title: str, markdown: str) -> None: ...
