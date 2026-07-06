from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class WatchState:
    alerted_signatures: set[str] = field(default_factory=set)
    recent_check_timestamps: list[str] = field(default_factory=list)
    consecutive_failures: int = 0
    consecutive_possible_blocks: int = 0
    last_status: str | None = None
    last_earliest_date: str | None = None
    last_checked_at: str | None = None

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
                },
                indent=2,
                sort_keys=True,
            )
        )

    def remember_check(self) -> None:
        now = utc_now_iso()
        self.last_checked_at = now
        self.recent_check_timestamps.append(now)
        cutoff = datetime.now(timezone.utc).timestamp() - 3600
        kept: list[str] = []
        for ts in self.recent_check_timestamps:
            try:
                if datetime.fromisoformat(ts).timestamp() >= cutoff:
                    kept.append(ts)
            except ValueError:
                continue
        self.recent_check_timestamps = kept

    def checks_in_last_hour(self) -> int:
        return len(self.recent_check_timestamps)
