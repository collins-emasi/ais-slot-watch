# AIS Slot Watcher

Notification-only watcher for earlier U.S. visa appointment slots on AIS/Yatri appointment pages.

It opens AIS with a local Playwright browser profile, reuses your own login session, checks for available calendar dates in your target window, and sends a desktop, push, or email alert. It does **not** store your AIS password, solve CAPTCHA, bypass rate limits, or reschedule for you.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configure

```bash
python -m slotwatcher init --config config.toml
```

Edit `config.toml`:

```toml
[watch]
appointment_url = "https://ais.usvisa-info.com/en-ke/niv/schedule/YOUR_SCHEDULE_ID/appointment?confirmed_limit_message=1&commit=Continue"
current_appointment_date = "2026-08-19"
earliest_allowed_date = "2026-07-06"
latest_allowed_date = "2026-08-18"
```

### Laptop alerts on macOS

Desktop notifications are enabled by default when the watcher runs on your Mac:

```toml
[notify]
desktop_notifications = true
desktop_sound = "Glass"
```

Then test:

```bash
python -m slotwatcher test-notify --config config.toml
```

macOS may ask whether Terminal, your Python app, or Script Editor can send notifications. Allow it in System Settings so future slot alerts appear like normal app notifications.

For the nicest Mac experience, install `terminal-notifier` separately. When it is available, slot alerts can open the AIS appointment page when you click the notification. Without it, the watcher falls back to macOS's built-in notification command.

### Easiest phone alerts with ntfy

1. Install the ntfy app on your phone.
2. Subscribe to a long, random topic, for example `garden-moonlight-42`.
3. Put that topic in `config.toml`:

```toml
[notify]
ntfy_topic = "garden-moonlight-42"
```

Then test:

```bash
python -m slotwatcher test-notify --config config.toml
```

## Log in once

```bash
python -m slotwatcher login --config config.toml
```

A browser opens. Sign in manually and navigate until the appointment page is visible. Press Enter in the terminal. Your session cookies stay in `.ais-browser-profile` on your machine.

## Run a single check

```bash
python -m slotwatcher once --config config.toml
```

## Watch continuously

```bash
python -m slotwatcher watch --config config.toml
```

Keep the machine awake. For always-on use, run it on a machine you control, such as a home server, a small VPS with a desktop session, or your laptop with sleep disabled.

The watcher persists its next scheduled check in the state file. If you stop it during a countdown and start it again, it resumes that countdown instead of immediately spending another AIS request. If the saved time has already passed, it checks right away.

## Design notes

- Browser session, not stored credentials.
- Calendar JSON endpoint probing when the page exposes a facility id.
- Visible page fallback if the endpoint changes.
- Deduplicated alerts, so the same date does not spam you.
- Restart-safe countdowns, so stopping and starting the watcher preserves the next scheduled check.
- Exact rolling hourly check caps instead of a blunt one-hour delay whenever the cap is reached.
- Adaptive backoff for login expiry, possible blocks, repeated failures, and hourly check caps.
- Pluggable notifiers: console, desktop, ntfy, Telegram, email SMTP.

## Safer defaults

The default interval is 5 minutes with jitter and an hourly check cap. Lower intervals can create account or IP problems. This tool is intentionally notification-only; verify the slot on AIS before rescheduling manually.


## Session persistence

The login command saves cookies/localStorage to `ais-auth-state.json`. If checks say `login_required` immediately after login, delete `ais-auth-state.json`, run `python -m slotwatcher login --config config.toml` again, and make sure the appointment page itself is visible before pressing Enter.
