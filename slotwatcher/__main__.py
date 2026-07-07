from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import shutil
import sys

from .config import load_config
from .credentials import prompt_and_store_password
from .notify import Alert, build_notifiers, notify_all
from .watcher import run_login, run_once, run_watch


def copy_example_config(destination: Path) -> None:
    source = Path(__file__).resolve().parent.parent / "config.example.toml"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite existing {destination}")
    shutil.copyfile(source, destination)
    print(f"Created {destination}. Edit it, then run: python -m slotwatcher login --config {destination}")


def print_result(result) -> None:
    print(f"status: {result.status}")
    print(f"earliest: {result.earliest.isoformat() if result.earliest else '-'}")
    print("candidate_dates:", ", ".join(d.isoformat() for d in result.candidate_dates) or "-")
    print("raw_dates:", ", ".join(d.isoformat() for d in result.raw_dates[:20]) or "-")
    print("sources:", ", ".join(sorted(result.sources)) or "-")
    print("facility_ids:", ", ".join(result.facility_ids) or "-")
    if result.times_by_date:
        print("times:")
        for day, times in result.times_by_date.items():
            print(f"  {day}: {', '.join(times)}")
    if result.notes:
        print("notes:")
        for note in result.notes:
            print(f"- {note}")


def print_notification_channels(notifiers) -> None:
    channels = [notifier.name for notifier in notifiers if notifier.name != "console"]
    if channels:
        print("Notification channels:", ", ".join(channels))
    else:
        print("No desktop/push/email notifier is active; console logging only.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Notification-only AIS U.S. visa slot watcher")
    parser.add_argument("command", choices=["init", "login", "store-password", "once", "watch", "test-notify"])
    parser.add_argument("--config", default="config.toml", help="Path to TOML config file")
    parser.add_argument("--send", action="store_true", help="For 'once', send notifications if a slot is found")
    parser.add_argument("--email", help="AIS account email for 'store-password'")
    args = parser.parse_args(argv)

    if args.command == "init":
        copy_example_config(Path(args.config))
        return 0

    config = load_config(args.config)
    notifiers = build_notifiers(config)

    if args.command == "login":
        run_login(config)
        return 0

    if args.command == "store-password":
        account = prompt_and_store_password(config, email=args.email)
        print(f"Stored AIS password in macOS Keychain for {account}.")
        return 0

    if args.command == "once":
        result = run_once(config, notifiers, notify=args.send)
        print_result(result)
        return 0

    if args.command == "watch":
        print(
            "Watching for dates from "
            f"{config.earliest_allowed_date.isoformat()} through "
            f"{config.current_appointment_date.isoformat()} (exclusive of current appointment)."
        )
        print_notification_channels(notifiers)
        run_watch(config, notifiers)
        return 0

    if args.command == "test-notify":
        print_notification_channels(notifiers)
        failures = notify_all(
            notifiers,
            Alert(
                title="AIS slot watcher test",
                body=f"Notification test sent on {date.today().isoformat()}.",
                url=config.appointment_url,
            ),
        )
        if failures:
            print("Some notification channels failed:")
            for failure in failures:
                print(f"- {failure}")
            return 1
        print("Test notification sent.")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
