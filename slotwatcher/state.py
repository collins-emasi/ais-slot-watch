from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class WatchState:
    alerted_signatures: set[str] = field(default_factory=set)
    recent_check_timestamps: list[str] = field(default_factory=list)
    consecutive_failures: int = 0
    consecutive_possible_blocks: int = 0
    last_status: str | None = None
    last_earliest_date: str | None = None
    last_checked_at: str | None = None
    next_check_at: str | None = None

    @classmethod
    def load(cls, path: Path) -> "WatchState":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text())
            return cls(
                alerted_signatures=set(raw.get("alerted_signatures", [])),
                recent_check_timestamps=list(raw.get("recent_check_timestamps", [])),
                consecutive_failures=int(raw.get("consecutive_failures", 0)),
                consecutive_possible_blocks=int(raw.get("consecutive_possible_blocks", 0)),
                last_status=raw.get("last_status"),
                last_earliest_date=raw.get("last_earliest_date"),
                last_checked_at=raw.get("last_checked_at"),
                next_check_at=raw.get("next_check_at"),
            )
        except Exception:
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "alerted_signatures": sorted(self.alerted_signatures),
                    "recent_check_timestamps": self.recent_check_timestamps[-200:],
                    "consecutive_failures": self.consecutive_failures,
                    "consecutive_possible_blocks": self.consecutive_possible_blocks,
                    "last_status": self.last_status,
                    "last_earliest_date": self.last_earliest_date,
                    "last_checked_at": self.last_checked_at,
                    "next_check_at": self.next_check_at,
                },
                indent=2,
                sort_keys=True,
            )
        )

    def prune_recent_checks(self, now: datetime | None = None) -> None:
        now = now or utc_now()
        cutoff = now - timedelta(hours=1)
        kept: list[str] = []
        for ts in self.recent_check_timestamps:
            parsed = parse_iso_datetime(ts)
            if parsed and parsed > cutoff:
                kept.append(parsed.isoformat())
        self.recent_check_timestamps = kept

    def remember_check(self, now: datetime | None = None) -> None:
        now = now or utc_now()
        now_iso = now.isoformat()
        self.last_checked_at = now_iso
        self.recent_check_timestamps.append(now_iso)
        self.prune_recent_checks(now)

    def checks_in_last_hour(self) -> int:
        self.prune_recent_checks()
        return len(self.recent_check_timestamps)

    def seconds_until_next_check_slot(self, max_checks_per_hour: int, now: datetime | None = None) -> int:
        if max_checks_per_hour <= 0:
            return 0

        now = now or utc_now()
        self.prune_recent_checks(now)
        recent = sorted(
            parsed
            for parsed in (parse_iso_datetime(ts) for ts in self.recent_check_timestamps)
            if parsed is not None
        )
        if len(recent) < max_checks_per_hour:
            return 0

        checks_to_expire = len(recent) - max_checks_per_hour + 1
        next_allowed_at = recent[checks_to_expire - 1] + timedelta(hours=1, seconds=1)
        return max(0, int((next_allowed_at - now).total_seconds()))

    def schedule_next_check(self, delay_seconds: int, now: datetime | None = None) -> None:
        now = now or utc_now()
        self.next_check_at = (now + timedelta(seconds=max(0, int(delay_seconds)))).isoformat()

    def clear_next_check(self) -> None:
        self.next_check_at = None

    def seconds_until_scheduled_check(self, now: datetime | None = None) -> int:
        scheduled = parse_iso_datetime(self.next_check_at)
        if not scheduled:
            return 0
        now = now or utc_now()
        return max(0, int((scheduled - now).total_seconds()))
