#!/usr/bin/env python3
"""One-time setup: authorize this app against your Yoto account.

Run this once (and again later if the stored refresh token ever stops
working):

    python authorize.py

It opens a browser for you to approve access (via a loopback redirect to
127.0.0.1:8787 — Yoto's dashboard-registered apps use the Authorization
Code + PKCE flow, not device code), then saves the refresh token to
state/token.json, which sleep_guard.py reads on every cron run. It also
prints each player's device_id so you can fill in `device_ids` in
config.ini if you want to limit this to specific players.
"""
import asyncio
import base64
import configparser
import hashlib
import json
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from yoto_api import YotoClient

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.ini"
TOKEN_PATH = ROOT / "state" / "token.json"

AUDIENCE = "https://api.yotoplay.com"
AUTHORIZE_URL = "https://login.yotoplay.com/authorize"
TOKEN_URL = "https://login.yotoplay.com/oauth/token"
REDIRECT_URI = "http://127.0.0.1:8787/callback"
SCOPE = "offline_access family:devices:view family:devices:control family:devices:manage"


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
            "(get one at https://dashboard.yoto.dev/)."
        )
    return client_id


def make_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        self.server.captured = params  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if "code" in params:
            body = b"<html><body>Authorized. You can close this tab and return to the terminal.</body></html>"
        else:
            body = b"<html><body>Authorization failed. Return to the terminal for details.</body></html>"
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # silence default request logging to stderr


def get_authorization_code(client_id: str, state: str, code_challenge: str) -> str:
    query = urlencode(
        {
            "audience": AUDIENCE,
            "scope": SCOPE,
            "response_type": "code",
            "client_id": client_id,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "redirect_uri": REDIRECT_URI,
            "state": state,
        }
    )
    auth_url = f"{AUTHORIZE_URL}?{query}"

    print("Opening your browser to approve access. If it doesn't open, visit:")
    print(f"  {auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("127.0.0.1", 8787), _CallbackHandler)
    server.captured = None  # type: ignore[attr-defined]
    print("Waiting for approval...")
    server.handle_request()  # blocks for exactly one request
    server.server_close()

    params = server.captured  # type: ignore[attr-defined]
    if params is None:
        sys.exit("No callback received.")
    if "error" in params:
        sys.exit(f"Authorization failed: {params.get('error_description', params['error'])}")
    if params.get("state", [None])[0] != state:
        sys.exit("State mismatch on callback; aborting for safety. Try again.")
    code = params.get("code", [None])[0]
    if not code:
        sys.exit(f"No authorization code in callback: {params}")
    return code


def exchange_code_for_tokens(client_id: str, code: str, code_verifier: str) -> dict:
    data = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code_verifier": code_verifier,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }
    ).encode("ascii")
    request = Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request) as response:
            return json.loads(response.read())
    except HTTPError as err:
        sys.exit(f"Token exchange failed: {err.code} {err.read().decode(errors='replace')}")


async def list_players_and_get_current_refresh_token(client_id: str, refresh_token: str) -> str:
    """List players (which itself uses the refresh token to mint an access
    token) and return whatever refresh token we should persist afterward.

    Auth0 rotates the refresh token on every use, so the one that came back
    from the code exchange is already stale the moment this call succeeds —
    callers must save client.token.refresh_token (returned here), not the
    token they passed in.
    """
    async with YotoClient(client_id=client_id) as client:
        client.set_refresh_token(refresh_token)
        await client.refresh()
        print("\nPlayers found on this account:")
        for device_id, player in client.players.items():
            print(f"  {device_id}  {player.name}")
        return client.token.refresh_token or refresh_token


def main() -> None:
    client_id = load_client_id()
    state = secrets.token_urlsafe(16)
    code_verifier, code_challenge = make_pkce_pair()

    code = get_authorization_code(client_id, state, code_challenge)
    tokens = exchange_code_for_tokens(client_id, code, code_verifier)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        sys.exit(
            "No refresh_token in the token response — make sure offline_access "
            f"is in SCOPE. Full response: {tokens}"
        )

    refresh_token = asyncio.run(
        list_players_and_get_current_refresh_token(client_id, refresh_token)
    )

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps({"refresh_token": refresh_token}, indent=2))
    TOKEN_PATH.chmod(0o600)
    print(f"\nRefresh token saved to {TOKEN_PATH}.")
    print(
        "\nTo limit this to specific players, copy their device_id(s) above "
        "into config.ini's device_ids (comma-separated)."
    )
    print(
        "\nNight hours come from each player's own day/night schedule in the "
        "Yoto app (Settings > that player > Sounds & Display) — nothing else "
        "to configure here."
    )


if __name__ == "__main__":
    main()
