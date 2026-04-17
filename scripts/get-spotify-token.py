#!/usr/bin/env python3
"""
One-shot Spotify refresh-token helper (runs on your laptop).

Prerequisites
-------------
1. Create a Spotify Developer app at https://developer.spotify.com/dashboard
2. In the app's "Redirect URIs" settings, add exactly:
       http://127.0.0.1:8888/callback
3. Save the Client ID + Client Secret shown on the app page.

Run
---
    python3 scripts/get-spotify-token.py <CLIENT_ID> <CLIENT_SECRET>

What it does
------------
* Opens your browser to Spotify's auth page.
* You click "Agree" to grant the app read-only access to your currently-playing
  + recently-played.
* Spotify redirects back to http://127.0.0.1:8888/callback?code=...
* This script catches the code, exchanges it for a refresh token, prints the
  values you need to paste as GitHub repo secrets, and exits.

No secrets are written to disk. Output goes to stdout only.
"""
from __future__ import annotations

import base64
import http.server
import json
import socketserver
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-currently-playing user-read-recently-played"
PORT = 8888


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <CLIENT_ID> <CLIENT_SECRET>", file=sys.stderr)
        print(f"       See docstring for details.", file=sys.stderr)
        return 2

    client_id, client_secret = sys.argv[1], sys.argv[2]

    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    })

    print("Opening your browser for Spotify authorization…")
    print(f"If it doesn't open, paste this URL manually:\n  {auth_url}\n")

    received: dict[str, str] = {}

    class Handler(http.server.SimpleHTTPRequestHandler):
        # Silence the default request log
        def log_message(self, *_):
            pass

        def do_GET(self):  # noqa: N802
            if not self.path.startswith("/callback"):
                self.send_response(404)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q:
                received["code"] = q["code"][0]
                body = b"<h2 style='font-family:sans-serif'>You can close this tab.</h2>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif "error" in q:
                received["error"] = q["error"][0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"error: {q['error'][0]}".encode())
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    webbrowser.open(auth_url)

    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Listening on {REDIRECT_URI} for the callback (Ctrl-C to abort)…")
        httpd.serve_forever()

    if "error" in received:
        print(f"\nSpotify returned error: {received['error']}", file=sys.stderr)
        return 1
    code = received.get("code")
    if not code:
        print("\nNo code received.", file=sys.stderr)
        return 1

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }).encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"\nToken exchange failed (HTTP {e.code}): {e.read().decode()}", file=sys.stderr)
        return 1

    print("\n" + "=" * 68)
    print("  SUCCESS — add these 3 secrets to your GitHub profile repo:")
    print("  https://github.com/m2moiz/m2moiz/settings/secrets/actions")
    print("=" * 68)
    print(f"  SPOTIFY_CLIENT_ID      = {client_id}")
    print(f"  SPOTIFY_CLIENT_SECRET  = {client_secret}")
    print(f"  SPOTIFY_REFRESH_TOKEN  = {data['refresh_token']}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
