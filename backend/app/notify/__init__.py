# Notifier 抽象：多渠道适配器（设计文档 §10）
from app.notify.base import Notifier
from app.notify.console import ConsoleNotifier
from app.notify.serverchan import ServerChanNotifier
from app.notify.smtp_email import EmailNotifier

__all__ = ["Notifier", "ConsoleNotifier", "ServerChanNotifier", "EmailNotifier"]
