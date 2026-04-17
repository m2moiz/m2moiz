#!/usr/bin/env python3
"""
generate-spotify.py
===================
Render the Spotify now-playing widget as a ~640x200 SVG.

Flow
----
1. Read SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_REFRESH_TOKEN from env.
2. Exchange refresh token → access token (1-hour validity).
3. GET /v1/me/player/currently-playing
   - 204/empty → fall back to /v1/me/player/recently-played?limit=1
4. Fetch album cover art, base64-encode for inline <image> embedding
   (avoids GitHub's Camo proxy re-fetching the cover separately, keeps
   the widget self-contained).
5. Render SVG with a big album-hero layout:
     | cover |   track title           | spotify
     | 140×  |   artist                |   mark
     |       |   ▶ Playing / recently  |
     |       └── equalizer bars ───────┘
6. Write to dist/spotify.svg.

The workflow then pushes dist/spotify.svg to the `output` branch.

Notes on IP: the Spotify circle-with-waves graphic below is a simplified
geometric representation (3 concentric arcs in a green disc) used widely
by third-party Spotify-integrated widgets; it's not the trademarked
"Spotify" wordmark logotype.
"""
from __future__ import annotations

import base64
import html as html_mod
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


# ============================================================================
# Spotify API
# ============================================================================

SPOTIFY_GREEN = "#1DB954"
BG = "#121212"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#B3B3B3"
TEXT_MUTED = "#7A7A7A"

CURRENTLY_PLAYING = "https://api.spotify.com/v1/me/player/currently-playing"
RECENTLY_PLAYED = "https://api.spotify.com/v1/me/player/recently-played?limit=1"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }).encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data["access_token"]


def spotify_get(url: str, token: str) -> dict | None:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 204:
                return None
            body = resp.read()
            if not body.strip():
                return None
            return json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code in (204, 404):
            return None
        raise


def fetch_image_b64(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return base64.b64encode(resp.read()).decode()
    except Exception as e:
        print(f"[warn] image fetch failed: {e}", file=sys.stderr)
        return None


def get_track_info(token: str) -> dict | None:
    data = spotify_get(CURRENTLY_PLAYING, token)
    if data and data.get("item"):
        item = data["item"]
        return _format_track(item, playing=bool(data.get("is_playing")))
    data = spotify_get(RECENTLY_PLAYED, token)
    if data and data.get("items"):
        item = data["items"][0]["track"]
        return _format_track(item, playing=False)
    return None


def _format_track(item: dict, playing: bool) -> dict:
    images = item.get("album", {}).get("images", [])
    # Prefer a medium image (not the largest) — 300x300 typically
    best = images[len(images) // 2] if images else None
    return {
        "playing": playing,
        "name": item.get("name", ""),
        "artists": ", ".join(a["name"] for a in item.get("artists", [])),
        "image_url": best["url"] if best else None,
        "track_url": item.get("external_urls", {}).get("spotify", ""),
    }


# ============================================================================
# SVG rendering
# ============================================================================

WIDTH = 640
HEIGHT = 200
CORNER = 14

COVER_X, COVER_Y, COVER_SIZE = 20, 30, 140

INFO_X = 180
TITLE_Y = 70
ARTIST_Y = 100
STATUS_Y = 125

# equalizer geometry
EQ_X0 = 180
EQ_X1 = 480
EQ_Y = 155
EQ_BARS = 24
EQ_MAX_H = 25
EQ_BAR_W = 6


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def render_equalizer(playing: bool) -> str:
    """Return SVG string for a row of green equalizer bars.

    Bars always animate — it's what makes the widget feel alive. We just vary
    the amplitude and speed based on play state:
      * playing   → full-amplitude (up to EQ_MAX_H), snappy timing
      * idle      → half-amplitude, slower timing, more opacity variation
    """
    step = (EQ_X1 - EQ_X0) / EQ_BARS
    bars: list[str] = []
    # Amplitude scale: idle bars are gentler but still visible.
    amp_scale = 1.0 if playing else 0.55
    speed_scale = 1.0 if playing else 1.8  # idle = slower
    opacity = 1.0 if playing else 0.70

    for i in range(EQ_BARS):
        x = EQ_X0 + i * step
        # Pseudo-random but deterministic amplitudes so the wave looks natural.
        a = (6 + (i * 7) % 14) * amp_scale          # low point
        b = (12 + (i * 11) % (EQ_MAX_H - 10)) * amp_scale  # mid
        c = (EQ_MAX_H - (i * 5) % 8) * amp_scale    # high point
        # Ensure minimum visibility so even the low-point bar is readable.
        a = max(a, 5)
        dur = (0.7 + (i * 0.09) % 0.6) * speed_scale
        begin = (i * 0.13) % 1.0

        heights = f"{a:.1f};{c:.1f};{b:.1f};{a:.1f}"
        ys = ";".join(f"{EQ_Y - h:.1f}" for h in (a, c, b, a))
        body = (
            f'<animate attributeName="height" values="{heights}" '
            f'dur="{dur:.2f}s" begin="{begin:.2f}s" repeatCount="indefinite"/>'
            f'<animate attributeName="y" values="{ys}" '
            f'dur="{dur:.2f}s" begin="{begin:.2f}s" repeatCount="indefinite"/>'
        )

        # Initial state = low point so the first frame already looks sensible.
        bars.append(
            f'<rect x="{x:.1f}" y="{EQ_Y - a:.1f}" width="{EQ_BAR_W}" '
            f'height="{a:.1f}" rx="1.5" fill="{SPOTIFY_GREEN}" opacity="{opacity}">{body}</rect>'
        )
    return "\n    ".join(bars)


def render_spotify_mark() -> str:
    """Simplified Spotify brand indicator: a green disc with 3 concentric
    curved lines. Placed to the right of the info column."""
    # disc center
    cx, cy, r = 560, 90, 30
    return f'''<g aria-hidden="true">
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="{SPOTIFY_GREEN}"/>
    <path d="M {cx - 16} {cy - 6} Q {cx} {cy - 16} {cx + 16} {cy - 6}"
          stroke="#000" stroke-width="3.2" fill="none" stroke-linecap="round"/>
    <path d="M {cx - 12} {cy}     Q {cx} {cy - 7}  {cx + 12} {cy}"
          stroke="#000" stroke-width="2.6" fill="none" stroke-linecap="round"/>
    <path d="M {cx - 9}  {cy + 6} Q {cx} {cy}      {cx + 9}  {cy + 6}"
          stroke="#000" stroke-width="2.0" fill="none" stroke-linecap="round"/>
  </g>
  <text x="{cx}" y="{cy + 50}" text-anchor="middle"
        font-family="ui-sans-serif, -apple-system, system-ui, Inter, 'Segoe UI', Roboto, sans-serif"
        font-size="13" font-weight="600" fill="{SPOTIFY_GREEN}"
        letter-spacing="0.06em">SPOTIFY</text>'''


def render_cover(cover_b64: str | None) -> str:
    if cover_b64:
        return (
            f'<clipPath id="cover-clip">'
            f'<rect x="{COVER_X}" y="{COVER_Y}" width="{COVER_SIZE}" '
            f'height="{COVER_SIZE}" rx="10"/></clipPath>'
            f'<image x="{COVER_X}" y="{COVER_Y}" width="{COVER_SIZE}" '
            f'height="{COVER_SIZE}" clip-path="url(#cover-clip)" '
            f'preserveAspectRatio="xMidYMid slice" '
            f'href="data:image/jpeg;base64,{cover_b64}"/>'
        )
    return (
        f'<rect x="{COVER_X}" y="{COVER_Y}" width="{COVER_SIZE}" '
        f'height="{COVER_SIZE}" rx="10" fill="#2A2A2A"/>'
        f'<text x="{COVER_X + COVER_SIZE/2}" y="{COVER_Y + COVER_SIZE/2 + 6}" '
        f'text-anchor="middle" font-size="48" fill="#555">♪</text>'
    )


def render_svg(info: dict | None) -> str:
    if info is None:
        return render_idle_svg()

    cover = render_cover(fetch_image_b64(info["image_url"]) if info["image_url"] else None)
    title = html_mod.escape(_truncate(info["name"], 36))
    artist = html_mod.escape(_truncate(info["artists"], 44))
    status = "▶ NOW PLAYING" if info["playing"] else "◦ RECENTLY PLAYED"
    eq = render_equalizer(info["playing"])
    mark = render_spotify_mark()

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}"
     viewBox="0 0 {WIDTH} {HEIGHT}" role="img"
     aria-label="Spotify: {title} — {artist}">
  <defs>
    <style>
      .title  {{ font: 600 22px ui-sans-serif, -apple-system, system-ui, Inter, "Segoe UI", Roboto, sans-serif; fill: {TEXT_PRIMARY}; }}
      .artist {{ font: 400 17px ui-sans-serif, -apple-system, system-ui, Inter, "Segoe UI", Roboto, sans-serif; fill: {TEXT_SECONDARY}; }}
      .status {{ font: 600 10px ui-sans-serif, -apple-system, system-ui, Inter, "Segoe UI", Roboto, sans-serif; fill: {SPOTIFY_GREEN}; letter-spacing: 0.14em; }}
    </style>
  </defs>
  <rect width="{WIDTH}" height="{HEIGHT}" rx="{CORNER}" fill="{BG}"/>
  {cover}
  <text x="{INFO_X}" y="{TITLE_Y}"  class="title">{title}</text>
  <text x="{INFO_X}" y="{ARTIST_Y}" class="artist">{artist}</text>
  <text x="{INFO_X}" y="{STATUS_Y}" class="status">{status}</text>
  {eq}
  {mark}
</svg>
'''


def render_idle_svg() -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}"
     viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="Spotify idle">
  <rect width="{WIDTH}" height="{HEIGHT}" rx="{CORNER}" fill="{BG}"/>
  <text x="{WIDTH/2}" y="{HEIGHT/2 + 6}" text-anchor="middle"
        font-family="ui-sans-serif, system-ui, sans-serif" font-size="16"
        fill="{TEXT_MUTED}">— idle —</text>
</svg>
'''


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    try:
        client_id     = os.environ["SPOTIFY_CLIENT_ID"]
        client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
        refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]
    except KeyError as e:
        print(f"[fatal] missing env var: {e}", file=sys.stderr)
        return 2

    try:
        token = get_access_token(client_id, client_secret, refresh_token)
    except Exception as e:
        print(f"[fatal] failed to get access token: {e}", file=sys.stderr)
        return 1

    try:
        info = get_track_info(token)
    except Exception as e:
        print(f"[warn] failed to fetch track info: {e}", file=sys.stderr)
        info = None

    svg = render_svg(info)
    out_path = Path("dist/spotify.svg")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")

    if info:
        marker = "playing" if info["playing"] else "recent"
        print(f"[ok] {info['name']} — {info['artists']} ({marker})")
    else:
        print("[ok] no track, rendered idle widget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
