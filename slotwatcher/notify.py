from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import smtplib
from typing import Protocol

import requests

from .config import WatchConfig


@dataclass(frozen=True)
class Alert:
    title: str
    body: str
    url: str | None = None
    tags: tuple[str, ...] = ("calendar", "bell")
    priority: str = "high"


class Notifier(Protocol):
    name: str

    def send(self, alert: Alert) -> None: ...


@dataclass
class ConsoleNotifier:
    name: str = "console"

    def send(self, alert: Alert) -> None:
        print("\n" + "=" * 72)
        print(alert.title)
        print("-" * 72)
        print(alert.body)
        if alert.url:
            print(alert.url)
        print("=" * 72 + "\n")


@dataclass
class NtfyNotifier:
    topic: str
    server: str = "https://ntfy.sh"
    name: str = "ntfy"

    def send(self, alert: Alert) -> None:
        url = f"{self.server.rstrip('/')}/{self.topic.strip()}"
        headers = {
            "Title": alert.title,
            "Priority": alert.priority,
            "Tags": ",".join(alert.tags),
        }
        if alert.url:
            headers["Click"] = alert.url
        response = requests.post(url, data=alert.body.encode("utf-8"), headers=headers, timeout=20)
        response.raise_for_status()


@dataclass
class TelegramNotifier:
    bot_token: str
    chat_id: str
    name: str = "telegram"

    def send(self, alert: Alert) -> None:
        text = f"*{alert.title}*\n\n{alert.body}"
        if alert.url:
            text += f"\n\nOpen AIS: {alert.url}"
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=20,
        )
        response.raise_for_status()


@dataclass
class EmailNotifier:
    smtp_host: str
    smtp_port: int
    email_from: str
    email_to: str
    smtp_user: str | None = None
    smtp_password: str | None = None
    name: str = "email"

    def send(self, alert: Alert) -> None:
        msg = EmailMessage()
        msg["From"] = self.email_from
        msg["To"] = self.email_to
        msg["Subject"] = alert.title
        body = alert.body
        if alert.url:
            body += f"\n\nOpen AIS: {alert.url}"
        msg.set_content(body)

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
            server.starttls()
            if self.smtp_user and self.smtp_password:
                server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)


def build_notifiers(config: WatchConfig) -> list[Notifier]:
    notifiers: list[Notifier] = [ConsoleNotifier()]

    if config.ntfy_topic:
        notifiers.append(NtfyNotifier(topic=config.ntfy_topic, server=config.ntfy_server))

    if config.telegram_bot_token and config.telegram_chat_id:
        notifiers.append(
            TelegramNotifier(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
            )
        )

    if config.email_to and config.smtp_host and config.email_from:
        notifiers.append(
            EmailNotifier(
                smtp_host=config.smtp_host,
                smtp_port=config.smtp_port,
                email_from=config.email_from,
                email_to=config.email_to,
                smtp_user=config.smtp_user,
                smtp_password=config.smtp_password,
            )
        )

    return notifiers


def notify_all(notifiers: list[Notifier], alert: Alert) -> list[str]:
    failures: list[str] = []
    for notifier in notifiers:
        try:
            notifier.send(alert)
        except Exception as exc:  # noqa: BLE001 - notification should not crash monitoring
            failures.append(f"{notifier.name}: {exc}")
    return failures
