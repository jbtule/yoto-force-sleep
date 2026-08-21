# yoto-force-sleep

Force a [Yoto](https://yotoplay.com) player's sleep timer on if playback
starts during that player's own night hours and the timer isn't already
running.

No server, no daemon — just a script you run from your own cron every few
minutes. Each run:

1. Checks each player's live state over Yoto's API.
2. Skips it unless it's online, currently in **night mode**, and
   **playing** with **no sleep timer already active**.
3. If all three hold, sets the sleep timer.

"Night hours" isn't configured in this repo — it reads the player's
`day_mode`, which the player itself reports based on the day/night schedule
you've already set in the Yoto app (that player's Settings > Sounds &
Display). Change your night hours there; this script just follows it.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

1. Get a client ID: <https://yoto.dev/get-started/start-here/>
2. `cp config.example.ini config.ini` and fill in `client_id`.
3. Run the one-time authorization:

   ```bash
   python authorize.py
   ```

   It prints a URL — open it, approve access, and it saves a refresh token
   to `state/token.json`. It also prints your players' `device_id`s; if you
   only want this to manage some of them, copy those IDs into `config.ini`'s
   `device_ids` (comma-separated). Leave it blank to cover every player on
   the account.

4. Try it once by hand:

   ```bash
   python sleep_guard.py
   ```

   It logs what it saw for each player and what it did (or didn't do).

## Running it on a schedule

Add a cron entry (`crontab -e`) to run it every 5 minutes:

```cron
*/5 * * * * cd /path/to/yoto-force-sleep && .venv/bin/python sleep_guard.py >> sleep_guard.log 2>&1
```

5 minutes is a reasonable floor: each run holds a live connection open for
a few seconds (`settle_seconds` in `config.ini`) to get fresh playback
state, so running it much more often than that doesn't buy you a faster
reaction — it just spends more of your night's battery/bandwidth on
polling. Expect up to a few minutes of lag between pressing play and the
sleep timer kicking in.

## Notes

- The refresh token Yoto issues rotates on each use; `sleep_guard.py`
  re-saves it to `state/token.json` after every run, so don't run two
  copies concurrently against the same `state/` directory.
- `config.ini` and `state/` hold credentials — both are gitignored. Don't
  commit them.
- If auth ever breaks (revoked access, expired refresh token), just rerun
  `python authorize.py`.
