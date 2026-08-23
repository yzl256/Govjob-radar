# 邮件兜底渠道（HTML 日报完整版）。
# 配置（.env）：SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / NOTIFY_EMAIL_TO
from __future__ import annotations

import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from app.notify.base import Notifier


class EmailNotifier(Notifier):
    name = "email"

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        to: str | None = None,
    ):
        self.host = host or os.environ.get("SMTP_HOST", "")
        self.port = int(port or os.environ.get("SMTP_PORT", "465"))
        self.user = user or os.environ.get("SMTP_USER", "")
        self.password = password or os.environ.get("SMTP_PASS", "")
        self.to = to or os.environ.get("NOTIFY_EMAIL_TO", "")

    def send(self, title: str, markdown: str) -> None:
        if not (self.host and self.user and self.password and self.to):
            raise RuntimeError("SMTP 未配置完整")
        msg = MIMEText(f"<pre style='white-space:pre-wrap'>{markdown}</pre>", "html", "utf-8")
        msg["Subject"] = Header(title, "utf-8")
        msg["From"] = self.user
        msg["To"] = self.to
        with smtplib.SMTP_SSL(self.host, self.port, timeout=20) as s:
            s.login(self.user, self.password)
            s.sendmail(self.user, [self.to], msg.as_string())
