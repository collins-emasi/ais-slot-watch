from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import hashlib
import platform
import re
import shutil
import smtplib
import subprocess
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


def _compact_body(body: str, *, limit: int = 260) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    text = " | ".join(lines[:4]) if lines else body.strip()
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _run_notification_command(command: list[str]) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"{command[0]} failed{suffix}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{command[0]} timed out") from exc


def _apple_script_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


@dataclass
class DesktopNotifier:
    sound: str | None = "Glass"
    app_name: str = "AIS Slot Watcher"
    name: str = "desktop"

    @staticmethod
    def is_supported() -> bool:
        system = platform.system()
        if system == "Darwin":
            return bool(shutil.which("terminal-notifier") or shutil.which("osascript"))
        if system == "Linux":
            return bool(shutil.which("notify-send"))
        return False

    def send(self, alert: Alert) -> None:
        message = _compact_body(alert.body)
        system = platform.system()
        if system == "Darwin":
            self._send_macos(alert, message)
            return
        if system == "Linux":
            self._send_linux(alert, message)
            return
        raise RuntimeError(f"desktop notifications are not supported on {system or 'this platform'}")

    def _send_macos(self, alert: Alert, message: str) -> None:
        terminal_notifier = shutil.which("terminal-notifier")
        if terminal_notifier:
            group = hashlib.sha1(f"{alert.title}:{alert.url or ''}".encode("utf-8")).hexdigest()[:16]
            command = [
                terminal_notifier,
                "-title",
                self.app_name,
                "-subtitle",
                alert.title,
                "-message",
                message,
                "-group",
                group,
            ]
            if self.sound:
                command.extend(["-sound", self.sound])
            if alert.url:
                command.extend(["-open", alert.url])
            _run_notification_command(command)
            return

        osascript = shutil.which("osascript")
        if not osascript:
            raise RuntimeError("osascript is not available")

        script = (
            f"display notification {_apple_script_string(message)} "
            f"with title {_apple_script_string(alert.title)}"
        )
        if alert.url:
            script += f" subtitle {_apple_script_string('Open AIS from the watcher output')}"
        if self.sound:
            script += f" sound name {_apple_script_string(self.sound)}"
        _run_notification_command([osascript, "-e", script])

    def _send_linux(self, alert: Alert, message: str) -> None:
        notify_send = shutil.which("notify-send")
        if not notify_send:
            raise RuntimeError("notify-send is not available")
        urgency = "critical" if alert.priority in {"high", "urgent"} else "normal"
        command = [notify_send, "--app-name", self.app_name, "--urgency", urgency, alert.title, message]
        _run_notification_command(command)


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

    if config.desktop_notifications and DesktopNotifier.is_supported():
        notifiers.append(DesktopNotifier(sound=config.desktop_sound))

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
