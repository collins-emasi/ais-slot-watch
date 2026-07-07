from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from pathlib import Path
import random
import re
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from .config import WatchConfig
from .dates import extract_dates_from_json, extract_dates_from_text, filter_target_dates
from .notify import Alert, Notifier, notify_all
from .state import WatchState, parse_iso_datetime

LOGGER = logging.getLogger("slotwatcher")

LOGIN_PATTERNS = (
    "sign in or create an account",
    "email address",
    "forgot your password",
    "enter your email",
)

BLOCK_PATTERNS = (
    "too many requests",
    "rate limit",
    "temporarily unavailable",
    "temporarily unable",
    "access denied",
    "support id",
    "blocked",
    "hard block",
    "soft block",
)

NO_SLOT_PATTERNS = (
    "there are no available appointments",
    "no appointments available",
    "no available appointments at the selected location",
)

TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")


@dataclass
class CheckResult:
    status: str
    candidate_dates: list[date] = field(default_factory=list)
    raw_dates: list[date] = field(default_factory=list)
    times_by_date: dict[str, list[str]] = field(default_factory=dict)
    facility_ids: list[str] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def earliest(self) -> date | None:
        return self.candidate_dates[0] if self.candidate_dates else None


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file)],
    )


def appointment_base_url(appointment_url: str) -> str:
    parsed = urlparse(appointment_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/appointment"):
        # Keep this permissive; the user may paste a URL with query params or a redirect.
        marker = "/appointment"
        if marker in path:
            path = path[: path.index(marker) + len(marker)]
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def text_lower(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3_000).lower()
    except Exception:
        return ""


def looks_login_required(page: Page) -> bool:
    url = page.url.lower()
    if "/users/sign_in" in url or "/users/sign_in" in url.replace("%2f", "/"):
        return True
    body = text_lower(page)
    return any(pattern in body for pattern in LOGIN_PATTERNS)


def looks_blocked(page: Page, extra_text: str = "") -> bool:
    body = (text_lower(page) + "\n" + extra_text.lower())[:80_000]
    return any(pattern in body for pattern in BLOCK_PATTERNS)


def discover_facility_ids(page: Page, configured_facility_id: str | None = None) -> list[str]:
    ids: set[str] = set()
    if configured_facility_id:
        ids.add(str(configured_facility_id).strip())

    selectors = [
        'select[name*="facility"] option',
        'select[id*="facility"] option',
        'select[name*="consulate"] option',
        'select[id*="consulate"] option',
    ]
    for selector in selectors:
        try:
            options = page.locator(selector)
            count = min(options.count(), 25)
            for idx in range(count):
                value = options.nth(idx).get_attribute("value")
                if value and value.strip().isdigit():
                    ids.add(value.strip())
        except Exception:
            continue

    return sorted(ids)


def extract_times_from_json(value: Any) -> list[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            found.update(extract_times_from_json(child))
    elif isinstance(value, list):
        for child in value:
            found.update(extract_times_from_json(child))
    elif isinstance(value, str):
        found.update(match.group(0) for match in TIME_RE.finditer(value))
    return sorted(found)


def probe_ais_calendar_api(page: Page, config: WatchConfig) -> tuple[set[date], dict[str, list[str]], list[str], list[str], bool]:
    """Try the calendar JSON endpoints used by AIS/Yatri pages.

    This is intentionally conservative: it only calls endpoints attached to the user's own
    appointment page and any facility ids visible in that page. It does not guess or brute-force ids.
    """
    dates: set[date] = set()
    times_by_date: dict[str, list[str]] = {}
    notes: list[str] = []
    blocked = False

    base = appointment_base_url(config.appointment_url)
    facility_ids = discover_facility_ids(page, config.facility_id)
    if not facility_ids:
        notes.append("No facility id found yet; falling back to visible calendar parsing.")
        return dates, times_by_date, [], notes, blocked

    expedite = "true" if config.expedite else "false"
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": config.appointment_url,
    }

    for facility_id in facility_ids:
        days_url = f"{base}/days/{facility_id}.json?appointments%5Bexpedite%5D={expedite}"
        try:
            response = page.context.request.get(days_url, headers=headers, timeout=config.calendar_probe_timeout_ms)
            if response.status in (401, 403, 429):
                blocked = response.status in (403, 429)
                notes.append(f"Calendar API returned HTTP {response.status} for facility {facility_id}.")
                continue
            if not response.ok:
                notes.append(f"Calendar API returned HTTP {response.status} for facility {facility_id}.")
                continue
            payload = response.json()
            payload_dates = extract_dates_from_json(payload)
            dates.update(payload_dates)
            if payload_dates:
                notes.append(f"Calendar API returned {len(payload_dates)} date(s) for facility {facility_id}.")

            target_dates = filter_target_dates(
                payload_dates,
                earliest_allowed=config.earliest_allowed_date,
                current_appointment=config.current_appointment_date,
                latest_allowed=config.latest_allowed_date,
            )
            for candidate in target_dates[:3]:
                times_url = (
                    f"{base}/times/{facility_id}.json?date={candidate.isoformat()}"
                    f"&appointments%5Bexpedite%5D={expedite}"
                )
                try:
                    times_response = page.context.request.get(
                        times_url,
                        headers=headers,
                        timeout=config.calendar_probe_timeout_ms,
                    )
                    if not times_response.ok:
                        continue
                    times = extract_times_from_json(times_response.json())
                    if times:
                        times_by_date[candidate.isoformat()] = times
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"Could not read times for {candidate}: {exc}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Calendar API probe failed for facility {facility_id}: {exc}")

    return dates, times_by_date, facility_ids, notes, blocked


def prime_visible_calendar(page: Page) -> None:
    """Open the date picker, if present, so network listeners can observe calendar JSON."""
    selectors = [
        '#appointments_consulate_appointment_date',
        'input[name="appointments[consulate_appointment][date]"]',
        'input[id*="appointment_date"]',
        'input.hasDatepicker',
        'input[type="text"][autocomplete="off"]',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            locator.click(timeout=2_000, force=True)
            page.wait_for_timeout(1_500)
            return
        except Exception:
            continue


def check_once(page: Page, config: WatchConfig) -> CheckResult:
    network_dates: set[date] = set()
    network_notes: list[str] = []
    network_note_keys: set[str] = set()

    def record_response(response: Any) -> None:
        url = getattr(response, "url", "").lower()
        if "appointment" not in url:
            return
        if not any(marker in url for marker in (".json", "/days/", "/times/")):
            return
        try:
            headers = response.headers
            content_type = headers.get("content-type", "") if isinstance(headers, dict) else ""
            if ".json" not in url and "json" not in content_type:
                return
            payload = response.json()
            found = extract_dates_from_json(payload)
            if found:
                network_dates.update(found)
                note_key = f"{len(found)}:{response.url}"
                if note_key not in network_note_keys:
                    network_note_keys.add(note_key)
                    network_notes.append(f"Network response yielded {len(found)} date(s): {response.url}")
        except Exception:
            return

    page.on("response", record_response)
    try:
        page.goto(config.appointment_url, wait_until="domcontentloaded", timeout=config.page_load_timeout_ms)
        page.wait_for_timeout(2_000)

        if looks_login_required(page):
            return CheckResult(status="login_required", notes=["AIS login is required or the saved session expired."])

        api_dates, times_by_date, facility_ids, api_notes, api_blocked = probe_ais_calendar_api(page, config)

        # Only click/open the visible calendar if the direct calendar API did not work.
        # This avoids duplicate /days/<facility>.json requests on every check.
        if not api_dates:
            prime_visible_calendar(page)
            page.wait_for_timeout(2_000)
        else:
            page.wait_for_timeout(500)

        visible_text = page.locator("body").inner_text(timeout=5_000)
        text_dates = extract_dates_from_text(visible_text)

        raw_dates = sorted(api_dates | network_dates | text_dates)
        candidate_dates = filter_target_dates(
            raw_dates,
            earliest_allowed=config.earliest_allowed_date,
            current_appointment=config.current_appointment_date,
            latest_allowed=config.latest_allowed_date,
        )

        sources: set[str] = set()
        if api_dates:
            sources.add("calendar-api")
        if network_dates:
            sources.add("network-json")
        if text_dates:
            sources.add("visible-text")

        notes = api_notes + network_notes
        lowered = visible_text.lower()
        if any(pattern in lowered for pattern in NO_SLOT_PATTERNS):
            notes.append("AIS page says there are no available appointments for the selected location.")

        if api_blocked or looks_blocked(page, "\n".join(notes)):
            status = "possible_block"
        elif candidate_dates:
            status = "slot_found"
        else:
            status = "no_slot"

        return CheckResult(
            status=status,
            candidate_dates=candidate_dates,
            raw_dates=raw_dates,
            times_by_date=times_by_date,
            facility_ids=facility_ids,
            sources=sources,
            notes=notes,
        )
    finally:
        try:
            page.remove_listener("response", record_response)
        except Exception:
            pass


def alert_for_slots(config: WatchConfig, result: CheckResult) -> Alert:
    earliest = result.earliest
    assert earliest is not None
    times = result.times_by_date.get(earliest.isoformat(), [])
    date_lines = []
    for candidate in result.candidate_dates[:8]:
        candidate_times = result.times_by_date.get(candidate.isoformat(), [])
        suffix = f" ({', '.join(candidate_times[:5])})" if candidate_times else ""
        date_lines.append(f"- {candidate.isoformat()}{suffix}")

    body = (
        f"Earlier U.S. visa interview availability detected.\n\n"
        f"Current appointment: {config.current_appointment_date.isoformat()}\n"
        f"Earliest detected: {earliest.isoformat()}"
        f"{f' at {', '.join(times[:5])}' if times else ''}\n\n"
        f"Candidate date(s):\n" + "\n".join(date_lines) + "\n\n"
        f"Action: open AIS, verify the slot, and reschedule manually if you want it.\n"
        f"Source: {', '.join(sorted(result.sources)) or 'page'}"
    )
    if result.notes:
        body += "\n\nNotes:\n" + "\n".join(f"- {note}" for note in result.notes[:5])

    return Alert(
        title=f"Earlier visa slot found: {earliest.isoformat()}",
        body=body,
        url=config.appointment_url,
        tags=("calendar", "rotating_light"),
        priority="urgent",
    )


def alert_for_status(config: WatchConfig, status: str, notes: list[str]) -> Alert:
    title_by_status = {
        "login_required": "AIS login needed for slot watcher",
        "possible_block": "AIS watcher is backing off",
        "error": "AIS watcher error",
    }
    body = "\n".join(notes) if notes else status
    if status == "login_required":
        body += "\n\nOpen the watcher with the login command, sign in manually, then restart watch mode."
    if status == "possible_block":
        body += "\n\nThe watcher will slow down automatically to avoid hammering the site."
    return Alert(title=title_by_status.get(status, f"AIS watcher: {status}"), body=body, url=config.appointment_url)


def slot_signature(result: CheckResult) -> str:
    if not result.earliest:
        return "slot:none"
    date_key = result.earliest.isoformat()
    times = ",".join(result.times_by_date.get(date_key, []))
    return f"slot:{date_key}:{times}"


def in_quiet_hours(config: WatchConfig, now: datetime | None = None) -> bool:
    if not config.quiet_start or not config.quiet_end:
        return False
    now = now or datetime.now()
    t = now.time()
    if config.quiet_start < config.quiet_end:
        return config.quiet_start <= t < config.quiet_end
    return t >= config.quiet_start or t < config.quiet_end


def seconds_until_quiet_ends(config: WatchConfig, now: datetime | None = None) -> int:
    now = now or datetime.now()
    if not config.quiet_end:
        return 0
    end_dt = datetime.combine(now.date(), config.quiet_end)
    if end_dt <= now:
        end_dt += timedelta(days=1)
    return max(60, int((end_dt - now).total_seconds()))


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def format_scheduled_time(value: str | None) -> str:
    scheduled = parse_iso_datetime(value)
    if not scheduled:
        return "unknown"
    return scheduled.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def compute_delay(config: WatchConfig, state: WatchState, result: CheckResult) -> int:
    if in_quiet_hours(config):
        return seconds_until_quiet_ends(config)

    base = config.interval_seconds

    if result.status == "possible_block":
        base = max(base, min(config.max_interval_seconds, 1800 * max(1, state.consecutive_possible_blocks)))
    elif result.status == "login_required":
        base = max(base, 900)
    elif result.status == "error":
        base = max(base, min(config.max_interval_seconds, config.interval_seconds * (2 ** min(4, state.consecutive_failures))))

    base = max(config.min_interval_seconds, min(config.max_interval_seconds, base))
    jitter = base * config.jitter_fraction
    delay = int(max(config.min_interval_seconds, min(config.max_interval_seconds, random.uniform(base - jitter, base + jitter))))

    rate_limit_delay = state.seconds_until_next_check_slot(config.max_checks_per_hour)
    if rate_limit_delay > delay:
        LOGGER.info(
            "Reached max_checks_per_hour=%s; next allowed check in %s.",
            config.max_checks_per_hour,
            format_duration(rate_limit_delay),
        )
        delay = rate_limit_delay

    return delay


def update_state_after_result(state: WatchState, result: CheckResult) -> None:
    state.remember_check()
    state.last_status = result.status
    state.last_earliest_date = result.earliest.isoformat() if result.earliest else None
    if result.status == "error":
        state.consecutive_failures += 1
    else:
        state.consecutive_failures = 0
    if result.status == "possible_block":
        state.consecutive_possible_blocks += 1
    else:
        state.consecutive_possible_blocks = 0



def create_check_context(browser: Browser, config: WatchConfig) -> BrowserContext:
    """Create a browser context for checks, preferring explicit saved auth state.

    Persistent profiles are convenient, but some sites do not reliably restore the
    logged-in session across separate launches. The login command now also writes
    a Playwright storage-state file containing cookies and localStorage. Loading
    that file here makes session reuse much more explicit and easier to debug.
    """
    kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}}
    if config.auth_state_file.exists():
        kwargs["storage_state"] = str(config.auth_state_file)
    return browser.new_context(**kwargs)


def save_auth_state(context: BrowserContext, config: WatchConfig) -> None:
    config.auth_state_file.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(config.auth_state_file))
    LOGGER.info("Saved AIS auth state to %s", config.auth_state_file)


def run_login(config: WatchConfig) -> None:
    setup_logging(config.log_file)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(config.appointment_url, wait_until="domcontentloaded", timeout=config.page_load_timeout_ms)
        print("Log in manually in the browser window. Navigate until the appointment page is visible.")
        print("Important: after login, make sure the appointment page itself is visible, not just the dashboard.")
        input("Press Enter here after you are logged in and can see the appointment page...")
        try:
            page.goto(config.appointment_url, wait_until="domcontentloaded", timeout=config.page_load_timeout_ms)
            page.wait_for_timeout(1_500)
            if looks_login_required(page):
                print("Warning: the page still looks logged out. The auth state was saved anyway, but the next check may ask you to log in again.")
            else:
                print("Login looks valid. Saving auth state.")
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: could not verify appointment page before saving auth state: {exc}")
        save_auth_state(context, config)
        context.close()
        browser.close()


def run_once(config: WatchConfig, notifiers: list[Notifier] | None = None, *, notify: bool = False) -> CheckResult:
    setup_logging(config.log_file)
    state = WatchState.load(config.state_file)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.headless)
        context = create_check_context(browser, config)
        page = context.new_page()
        try:
            result = check_once(page, config)
            update_state_after_result(state, result)
            if notify and notifiers:
                maybe_notify(config, state, result, notifiers)
                if result.status in {"no_slot", "slot_found"}:
                    context.storage_state(path=str(config.auth_state_file))
            state.save(config.state_file)
            return result
        finally:
            context.close()
            browser.close()


def sleep_with_countdown(seconds: int) -> None:
    """Sleep while updating the existing INFO-style countdown line."""
    remaining = int(seconds)

    try:
        while remaining > 0:
            mins, secs = divmod(remaining, 60)

            now = datetime.now()
            line = (
                f"{now.strftime('%Y-%m-%d %H:%M:%S')},{now.microsecond // 1000:03d} "
                f"INFO slotwatcher: Next check in {mins:02d}:{secs:02d}."
            )

            print(f"\r{line}", end="", flush=True)
            time.sleep(1)
            remaining -= 1
    except KeyboardInterrupt:
        print()
        raise

    print()


def wait_until_next_check(config: WatchConfig, state: WatchState, state_file: Path) -> None:
    scheduled_delay = state.seconds_until_scheduled_check()
    rate_limit_delay = state.seconds_until_next_check_slot(config.max_checks_per_hour)
    delay = max(scheduled_delay, rate_limit_delay)
    if delay <= 0:
        return

    if scheduled_delay >= rate_limit_delay and state.next_check_at:
        LOGGER.info(
            "Resuming scheduled wait; next check at %s.",
            format_scheduled_time(state.next_check_at),
        )
    else:
        LOGGER.info(
            "Hourly check cap is active; next allowed check in %s.",
            format_duration(rate_limit_delay),
        )

    state.save(state_file)
    sleep_with_countdown(delay)


def close_quietly(resource: Any, label: str) -> None:
    try:
        resource.close()
    except Exception as exc:  # noqa: BLE001 - cleanup should not turn Ctrl+C into a traceback
        LOGGER.debug("Ignoring error while closing %s: %s", label, exc)


def maybe_notify(config: WatchConfig, state: WatchState, result: CheckResult, notifiers: list[Notifier]) -> None:
    if result.status == "slot_found" and result.earliest:
        signature = slot_signature(result)
        if signature not in state.alerted_signatures:
            failures = notify_all(notifiers, alert_for_slots(config, result))
            if failures:
                LOGGER.warning("Notification failures: %s", failures)
            state.alerted_signatures.add(signature)
        return

    if result.status in {"login_required", "possible_block"}:
        signature = f"status:{result.status}:{datetime.now().date().isoformat()}"
        if signature not in state.alerted_signatures:
            failures = notify_all(notifiers, alert_for_status(config, result.status, result.notes))
            if failures:
                LOGGER.warning("Notification failures: %s", failures)
            state.alerted_signatures.add(signature)


def run_watch(config: WatchConfig, notifiers: list[Notifier]) -> None:
    setup_logging(config.log_file)
    state = WatchState.load(config.state_file)

    browser: Browser | None = None
    context: BrowserContext | None = None

    try:
        wait_until_next_check(config, state, config.state_file)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=config.headless)
            context = create_check_context(browser, config)
            page = context.new_page()

            while True:
                wait_until_next_check(config, state, config.state_file)

                try:
                    state.clear_next_check()
                    result = check_once(page, config)
                    LOGGER.info(
                        "status=%s earliest=%s candidates=%s sources=%s notes=%s",
                        result.status,
                        result.earliest,
                        [d.isoformat() for d in result.candidate_dates],
                        sorted(result.sources),
                        result.notes[:3],
                    )
                    update_state_after_result(state, result)
                    maybe_notify(config, state, result, notifiers)

                    if result.status in {"no_slot", "slot_found"}:
                        context.storage_state(path=str(config.auth_state_file))

                    state.save(config.state_file)
                    if result.status == "login_required":
                        LOGGER.warning("AIS session expired. Notifying user and stopping watcher.")
                        state.clear_next_check()
                        state.save(config.state_file)

                        alert = Alert(
                            title="AIS watcher stopped: login required",
                            body=(
                                "AIS slot watcher stopped because your login session expired.\n\n"
                                "Action needed:\n"
                                "1. Run: python -m slotwatcher login --config config.toml\n"
                                "2. Log in manually and open the appointment page\n"
                                "3. Restart: python -m slotwatcher watch --config config.toml"
                            ),
                            url=config.appointment_url,
                            tags=("warning", "lock"),
                            priority="high",
                        )

                        failures = notify_all(notifiers, alert)

                        if failures:
                            LOGGER.warning("Notification failures while reporting login expiry: %s", failures)
                            print("Notification failures while reporting login expiry:")
                            for failure in failures:
                                print(f"- {failure}")

                        return

                except PlaywrightTimeoutError as exc:
                    result = CheckResult(status="error", notes=[f"Page load timed out: {exc}"])
                    update_state_after_result(state, result)
                    maybe_notify(config, state, result, notifiers)
                    state.save(config.state_file)
                except Exception as exc:  # noqa: BLE001
                    result = CheckResult(status="error", notes=[repr(exc)])
                    update_state_after_result(state, result)
                    maybe_notify(config, state, result, notifiers)
                    state.save(config.state_file)

                delay = compute_delay(config, state, result)
                state.schedule_next_check(delay)
                state.save(config.state_file)
                sleep_with_countdown(delay)
    except KeyboardInterrupt:
        LOGGER.info("Watcher stopped by user. Next scheduled check is preserved.")
    finally:
        if context is not None:
            close_quietly(context, "browser context")
        if browser is not None:
            close_quietly(browser, "browser")
