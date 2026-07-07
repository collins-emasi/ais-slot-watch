from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
import os
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def _parse_date(value: str | date | None, *, default: date | None = None) -> date:
    if value is None or value == "":
        if default is None:
            raise ValueError("missing required date")
        return default
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


@dataclass(frozen=True)
class WatchConfig:
    appointment_url: str
    current_appointment_date: date
    earliest_allowed_date: date = field(default_factory=date.today)
    latest_allowed_date: date | None = None
    facility_id: str | None = None
    expedite: bool = False

    # Browser/session behavior
    profile_dir: Path = Path(".ais-browser-profile")
    headless: bool = False
    page_load_timeout_ms: int = 45_000
    calendar_probe_timeout_ms: int = 8_000

    # AIS sign-in recovery. Passwords are read from env/keychain, not TOML.
    auto_login: bool = False
    login_email: str | None = None
    login_password: str | None = None
    keychain_service: str = "ais-slot-watch"
    keychain_account: str | None = None

    # Polling behavior
    interval_seconds: int = 300
    min_interval_seconds: int = 90
    max_interval_seconds: int = 3600
    jitter_fraction: float = 0.25
    max_checks_per_hour: int = 18
    quiet_start: time | None = None
    quiet_end: time | None = None

    # State/logging
    state_file: Path = Path("slotwatcher-state.json")
    log_file: Path = Path("slotwatcher.log")
    auth_state_file: Path = Path("ais-auth-state.json")

    # Notification config
    desktop_notifications: bool = True
    desktop_sound: str | None = "Glass"
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    email_to: str | None = None
    email_from: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None

    def target_end(self) -> date:
        return self.latest_allowed_date or self.current_appointment_date

    def notification_enabled(self) -> bool:
        return bool(
            self.desktop_notifications
            or self.ntfy_topic
            or (self.telegram_bot_token and self.telegram_chat_id)
            or (self.email_to and self.smtp_host and self.email_from)
        )


def _env(key: str, default: Any = None) -> Any:
    value = os.getenv(key)
    return default if value in (None, "") else value


def load_config(path: str | Path) -> WatchConfig:
    path = Path(path)
    raw: dict[str, Any] = {}
    if path.exists():
        raw = tomllib.loads(path.read_text())

    watch = raw.get("watch", {})
    browser = raw.get("browser", {})
    auth = raw.get("auth", {})
    polling = raw.get("polling", {})
    notify = raw.get("notify", {})
    state = raw.get("state", {})

    current = _parse_date(
        _env("AIS_CURRENT_APPOINTMENT_DATE", watch.get("current_appointment_date"))
    )
    earliest = _parse_date(
        _env("AIS_EARLIEST_ALLOWED_DATE", watch.get("earliest_allowed_date")),
        default=date.today(),
    )
    latest = _parse_date(
        _env("AIS_LATEST_ALLOWED_DATE", watch.get("latest_allowed_date")),
        default=current,
    )

    return WatchConfig(
        appointment_url=_env("AIS_APPOINTMENT_URL", watch.get("appointment_url")),
        current_appointment_date=current,
        earliest_allowed_date=earliest,
        latest_allowed_date=latest,
        facility_id=_env("AIS_FACILITY_ID", watch.get("facility_id")),
        expedite=_parse_bool(_env("AIS_EXPEDITE", watch.get("expedite")), default=False),
        profile_dir=Path(_env("AIS_PROFILE_DIR", browser.get("profile_dir", ".ais-browser-profile"))),
        headless=_parse_bool(_env("AIS_HEADLESS", browser.get("headless")), default=False),
        page_load_timeout_ms=int(_env("AIS_PAGE_LOAD_TIMEOUT_MS", browser.get("page_load_timeout_ms", 45_000))),
        calendar_probe_timeout_ms=int(_env("AIS_CALENDAR_PROBE_TIMEOUT_MS", browser.get("calendar_probe_timeout_ms", 8_000))),
        auto_login=_parse_bool(_env("AIS_AUTO_LOGIN", auth.get("auto_login")), default=False),
        login_email=_env("AIS_LOGIN_EMAIL", auth.get("login_email")),
        login_password=_env("AIS_LOGIN_PASSWORD"),
        keychain_service=_env("AIS_KEYCHAIN_SERVICE", auth.get("keychain_service", "ais-slot-watch")),
        keychain_account=_env("AIS_KEYCHAIN_ACCOUNT", auth.get("keychain_account")),
        interval_seconds=int(_env("AIS_INTERVAL_SECONDS", polling.get("interval_seconds", 300))),
        min_interval_seconds=int(_env("AIS_MIN_INTERVAL_SECONDS", polling.get("min_interval_seconds", 90))),
        max_interval_seconds=int(_env("AIS_MAX_INTERVAL_SECONDS", polling.get("max_interval_seconds", 3600))),
        jitter_fraction=float(_env("AIS_JITTER_FRACTION", polling.get("jitter_fraction", 0.25))),
        max_checks_per_hour=int(_env("AIS_MAX_CHECKS_PER_HOUR", polling.get("max_checks_per_hour", 18))),
        quiet_start=_parse_time(_env("AIS_QUIET_START", polling.get("quiet_start"))),
        quiet_end=_parse_time(_env("AIS_QUIET_END", polling.get("quiet_end"))),
        state_file=Path(_env("AIS_STATE_FILE", state.get("state_file", "slotwatcher-state.json"))),
        log_file=Path(_env("AIS_LOG_FILE", state.get("log_file", "slotwatcher.log"))),
        auth_state_file=Path(_env("AIS_AUTH_STATE_FILE", state.get("auth_state_file", "ais-auth-state.json"))),
        desktop_notifications=_parse_bool(_env("DESKTOP_NOTIFICATIONS", notify.get("desktop_notifications")), default=True),
        desktop_sound=_env("DESKTOP_SOUND", notify.get("desktop_sound", "Glass")) or None,
        ntfy_topic=_env("NTFY_TOPIC", notify.get("ntfy_topic")),
        ntfy_server=_env("NTFY_SERVER", notify.get("ntfy_server", "https://ntfy.sh")),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN", notify.get("telegram_bot_token")),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID", notify.get("telegram_chat_id")),
        email_to=_env("EMAIL_TO", notify.get("email_to")),
        email_from=_env("EMAIL_FROM", notify.get("email_from")),
        smtp_host=_env("SMTP_HOST", notify.get("smtp_host")),
        smtp_port=int(_env("SMTP_PORT", notify.get("smtp_port", 587))),
        smtp_user=_env("SMTP_USER", notify.get("smtp_user")),
        smtp_password=_env("SMTP_PASSWORD", notify.get("smtp_password")),
    )
