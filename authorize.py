#!/usr/bin/env python3
"""One-time setup: authorize this app against your Yoto account.

Run this once (and again later if the stored refresh token ever stops
working):

    python authorize.py

It walks you through the OAuth device-code flow (open a URL, approve on
your phone/computer) and saves the refresh token to state/token.json,
which sleep_guard.py reads on every cron run. It also prints each
player's device_id so you can fill in `device_ids` in config.ini if you
want to limit this to specific players.
"""
import asyncio
import configparser
import json
import sys
from pathlib import Path

from yoto_api import YotoClient

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.ini"
TOKEN_PATH = ROOT / "state" / "token.json"


def load_client_id() -> str:
    if not CONFIG_PATH.exists():
        sys.exit(
            f"Missing {CONFIG_PATH}. Copy config.example.ini to config.ini "
            "and fill in client_id first."
        )
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    client_id = config.get("yoto", "client_id", fallback="").strip()
    if not client_id:
        sys.exit(
            "Set client_id in config.ini first "
            "(get one at https://yoto.dev/get-started/start-here/)."
        )
    return client_id


async def main() -> None:
    client_id = load_client_id()
    async with YotoClient(client_id=client_id) as client:
        auth = await client.device_code_flow_start()
        print("Open this URL and approve access:")
        print(f"  {auth['verification_uri_complete']}")
        print("Waiting for approval...")
        token = await client.device_code_flow_complete(auth)

        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(
            json.dumps({"refresh_token": token.refresh_token}, indent=2)
        )
        TOKEN_PATH.chmod(0o600)

        await client.refresh()
        print("\nAuthorized. Players found on this account:")
        for device_id, player in client.players.items():
            print(f"  {device_id}  {player.name}")
        print(f"\nRefresh token saved to {TOKEN_PATH}.")
        print(
            "To limit this to specific players, copy their device_id(s) "
            "above into config.ini's device_ids (comma-separated)."
        )
        print(
            "\nNight hours come from each player's own day/night schedule "
            "in the Yoto app (Settings > that player > Sounds & Display) — "
            "nothing else to configure here."
        )


if __name__ == "__main__":
    asyncio.run(main())
