#!/usr/bin/env python3
"""Force the sleep timer on if a Yoto player starts playing during its own
night hours and the timer isn't already running.

Meant to be run from your own cron (see README.md), e.g. every 5 minutes.
Each run is a short-lived check: connect, ask each player for its live
state over MQTT, act if needed, disconnect. No server, no daemon.

"Night hours" isn't configured here — it reads each player's `day_mode`,
which the player itself reports based on the day/night schedule set in the
Yoto app (Settings > that player > Sounds & Display).
"""
import asyncio
import configparser
import json
import sys
from pathlib import Path

from yoto_api import DayMode, PlaybackStatus, YotoClient, YotoError

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.ini"
TOKEN_PATH = ROOT / "state" / "token.json"


def log(msg: str) -> None:
    from datetime import datetime

    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(
            f"Missing {CONFIG_PATH}. Copy config.example.ini to config.ini "
            "and fill in client_id (see README.md)."
        )
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    section = config["yoto"]
    client_id = section.get("client_id", "").strip()
    if not client_id:
        sys.exit("Set client_id in config.ini first.")
    device_ids = [d.strip() for d in section.get("device_ids", "").split(",") if d.strip()]
    return {
        "client_id": client_id,
        "device_ids": device_ids,
        "sleep_minutes": section.getint("sleep_minutes", fallback=30),
        "settle_seconds": section.getfloat("settle_seconds", fallback=4.0),
    }


def load_refresh_token() -> str:
    if not TOKEN_PATH.exists():
        sys.exit(
            f"Missing {TOKEN_PATH}. Run `python authorize.py` once first to "
            "authorize this app against your Yoto account."
        )
    data = json.loads(TOKEN_PATH.read_text())
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        sys.exit(f"{TOKEN_PATH} has no refresh_token. Run `python authorize.py` again.")
    return refresh_token


def save_refresh_token(refresh_token: str) -> None:
    TOKEN_PATH.write_text(json.dumps({"refresh_token": refresh_token}, indent=2))
    TOKEN_PATH.chmod(0o600)


async def run(config: dict) -> int:
    async with YotoClient(client_id=config["client_id"]) as client:
        client.set_refresh_token(load_refresh_token())
        await client.refresh()  # device list + per-player config/info

        target_ids = config["device_ids"] or list(client.players)
        missing = [d for d in target_ids if d not in client.players]
        for device_id in missing:
            log(f"WARNING device_id {device_id} in config.ini not found on this account, skipping")
        target_ids = [d for d in target_ids if d in client.players]
        if not target_ids:
            log("No players to check (empty account, or all configured device_ids are invalid).")
            return 1

        await client.connect_events(target_ids)
        await asyncio.sleep(config["settle_seconds"])

        for device_id in target_ids:
            player = client.players[device_id]
            name = player.name

            if not player.is_online:
                log(f"{name}: offline, skipping")
                continue

            day_mode = player.status.day_mode
            if day_mode is None:
                log(f"{name}: day/night mode unknown yet, skipping")
                continue
            if day_mode != DayMode.NIGHT:
                log(f"{name}: currently in day mode, skipping")
                continue

            playback_status = player.last_event.playback_status
            if playback_status != PlaybackStatus.PLAYING:
                log(f"{name}: not currently playing (status={playback_status}), skipping")
                continue

            sleep_active = player.last_event.sleep_timer_active
            if sleep_active is None:
                log(f"{name}: playing at night, but sleep timer state unknown "
                    "(no reply in time) — skipping this run to be safe")
                continue
            if sleep_active:
                log(f"{name}: playing at night, sleep timer already active — nothing to do")
                continue

            seconds = config["sleep_minutes"] * 60
            await client.set_sleep_timer(device_id, seconds)
            log(f"{name}: playing at night with no sleep timer — set it to {config['sleep_minutes']} min")

        await client.disconnect_events()

        # Auth0 rotates refresh tokens on use; persist whatever we ended up with.
        if client.token and client.token.refresh_token:
            save_refresh_token(client.token.refresh_token)

    return 0


def main() -> int:
    config = load_config()
    try:
        return asyncio.run(run(config))
    except YotoError as err:
        log(f"ERROR {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
