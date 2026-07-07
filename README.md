# AIS Slot Watcher

Notification-only watcher for earlier U.S. visa appointment slots on AIS/Yatri appointment pages.

It opens AIS with a local Playwright browser, reuses your own login session, checks for available calendar dates in your target window, and sends a desktop, push, or email alert. It does not put your AIS password in config files, solve CAPTCHA, bypass rate limits, reschedule appointments, or make decisions for you.

## What You Need

- Python 3.11 or newer.
- An AIS account that can already open your appointment page manually.
- Your current appointment date.
- The date window you care about.
- A machine that can stay awake while the watcher runs.

On macOS, the built-in desktop notifications work without extra packages. Phone push notifications are optional through ntfy.

## Install

Clone or open this project, then set up the Python environment:

```bash
git clone https://github.com/collins-emasi/ais-slot-watch.git
cd ais-slot-watch
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

If you already have the project folder, start at `cd ais-slot-watch`.

## Create Your Config

Create a private local config file:

```bash
python -m slotwatcher init --config config.toml
```

`config.toml` is ignored by git so your appointment URL, notification topics, and local settings do not get committed.

Edit the `[watch]` section:

```toml
[watch]
appointment_url = "https://ais.usvisa-info.com/en-ke/niv/schedule/YOUR_SCHEDULE_ID/appointment?confirmed_limit_message=1&commit=Continue"
current_appointment_date = "2026-08-19"
earliest_allowed_date = "2026-07-13"
latest_allowed_date = "2026-08-18"
facility_id = ""
expedite = false
```

Use your exact AIS appointment page URL. The easiest way is to log into AIS in your normal browser, open the appointment page, and copy the address bar. If a copied URL contains `&amp;`, change it back to `&`.

The date rules are:

- `current_appointment_date`: the appointment you already have.
- `earliest_allowed_date`: the first date you would accept.
- `latest_allowed_date`: the last date you would accept.
- The watcher alerts for dates from `earliest_allowed_date` through `latest_allowed_date`, and always earlier than `current_appointment_date`.
- Leave `facility_id` blank unless automatic facility detection fails.

## Set Up Notifications

Desktop notifications are enabled by default on macOS:

```toml
[notify]
desktop_notifications = true
desktop_sound = "Glass"
```

Test notifications before watching:

```bash
python -m slotwatcher test-notify --config config.toml
```

macOS may ask whether Terminal, Python, or Script Editor can send notifications. Allow it in System Settings.

For click-to-open Mac notifications, install `terminal-notifier` separately. Without it, the watcher uses macOS's built-in notification command.

### Optional Phone Alerts

For phone push alerts with ntfy:

1. Install the ntfy app on your phone.
2. Subscribe to a long random topic, for example `garden-moonlight-42`.
3. Put that same topic in `config.toml`:

```toml
[notify]
ntfy_topic = "garden-moonlight-42"
ntfy_server = "https://ntfy.sh"
```

Run `test-notify` again and confirm both your laptop and phone receive the alert.

## Log In Once

Save your current AIS browser session:

```bash
python -m slotwatcher login --config config.toml
```

A browser opens. Log in manually, navigate until the appointment page itself is visible, then press Enter in the terminal. The watcher saves cookies/localStorage to `ais-auth-state.json`.

Run one check:

```bash
python -m slotwatcher once --config config.toml
```

Good signs are `status: no_slot` or `status: slot_found`. If you see `login_required`, run the login command again and make sure the appointment page is visible before pressing Enter.

## Optional Automatic Session Recovery

AIS may expire the server session after about an hour. To let the watcher recover without manually running `login` each time, enable auto-login and store your password in macOS Keychain.

Edit `config.toml`:

```toml
[auth]
auto_login = true
login_email = "you@example.com"
keychain_service = "ais-slot-watch"
```

Store the password:

```bash
python -m slotwatcher store-password --config config.toml --email you@example.com
```

When AIS redirects to sign-in, the watcher fills your own email/password, submits the form, verifies that the appointment page is reachable again, saves fresh auth state, and keeps watching.

This does not bypass CAPTCHA, OTP, 2FA, or other interactive challenges. If AIS asks for one, the watcher notifies you and stops for manual login.

You can also provide the password through the environment instead of Keychain:

```bash
AIS_LOGIN_PASSWORD="your-password" python -m slotwatcher watch --config config.toml
```

Keychain is preferred on macOS because the password is not visible in your shell history.

## Watch Continuously

Run the watcher:

```bash
python -m slotwatcher watch --config config.toml
```

On macOS, use `caffeinate` if you want the laptop to stay awake while the watcher is running:

```bash
caffeinate -dimsu python -m slotwatcher watch --config config.toml
```

The watcher prints the active notification channels, checks AIS, then counts down to the next check. It persists the next scheduled check in `slotwatcher-state.json`, so if you stop it during a countdown and start it again, it resumes the countdown instead of immediately spending another AIS request.

When an earlier slot is detected, the watcher sends an alert. Open AIS yourself, verify the slot, and reschedule manually if you want it.

## Common Issues

### `login_required` immediately after login

Run:

```bash
python -m slotwatcher login --config config.toml
```

After signing in, make sure the appointment page is visible before pressing Enter. If it still fails, delete `ais-auth-state.json` and run the login command again.

### Notifications do not appear on macOS

Run:

```bash
python -m slotwatcher test-notify --config config.toml
```

Then check System Settings and allow notifications for the app macOS names in the prompt, often Terminal, Python, or Script Editor.

### Auto-login says no password was found

Store the password in Keychain:

```bash
python -m slotwatcher store-password --config config.toml --email you@example.com
```

Make sure `auth.login_email` matches that email, or set `keychain_account` in `config.toml`.

### AIS asks for CAPTCHA, OTP, or 2FA

The watcher will not bypass interactive challenges. Run the manual login command, complete the challenge yourself, and restart watch mode.

### The watcher checks too often or too slowly

Adjust `[polling]` in `config.toml`. The default is intentionally conservative:

```toml
[polling]
interval_seconds = 300
min_interval_seconds = 90
max_interval_seconds = 3600
jitter_fraction = 0.25
max_checks_per_hour = 18
```

Lower intervals can create account or IP problems. The watcher also enforces rolling hourly caps and backs off after possible blocks or repeated errors.

## Design Notes

- Browser session by default; optional auto-login reads the password from env or macOS Keychain.
- Calendar JSON endpoint probing when the page exposes a facility id.
- Visible page fallback if the endpoint changes.
- Deduplicated alerts, so the same date does not spam you.
- Restart-safe countdowns, so stopping and starting the watcher preserves the next scheduled check.
- Exact rolling hourly check caps instead of a blunt one-hour delay whenever the cap is reached.
- Adaptive backoff for login expiry, possible blocks, repeated failures, and hourly check caps.
- Auto-login stops at CAPTCHA/OTP/2FA instead of trying to bypass interactive challenges.
- Pluggable notifiers: console, desktop, ntfy, Telegram, email SMTP.
