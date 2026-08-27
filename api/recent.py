"""
Recently played tracks widget.

Renders the last N tracks played on Spotify as a compact SVG list, sized to sit
directly beneath the now-playing card from orchestrator.py (same 540px width).
"""

import os
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from flask import Flask, Response, request

from .spotify import get_recent_tracks, is_configured

app = Flask(__name__)

# Layout constants (viewBox units, matching the now-playing card's 540 width)
WIDTH = 540
PAD_X = 16
HEADER_H = 30
ROW_H = 44
ART = 32
BOTTOM_PAD = 10

DEFAULT_COUNT = 5
MAX_COUNT = 10

ACCENT = "#1db954"

# Text pairs for dark and light backgrounds, picked per-request by luminance so
# the card stays readable whatever background_color the caller asks for.
TEXT_ON_DARK, MUTED_ON_DARK = "#e6edf3", "#8b949e"
TEXT_ON_LIGHT, MUTED_ON_LIGHT = "#1f2328", "#57606a"

# Widest strings that fit before the played-at column, in characters.
TITLE_CHARS = 38
ARTIST_CHARS = 44


def escape_xml(text: str) -> str:
    """Escape the five XML entities so track titles can't break the document."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def truncate(text: str, limit: int) -> str:
    """Shorten text to limit characters, adding an ellipsis when cut."""
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clean_hex(value: Optional[str], fallback: str) -> str:
    """Accept a bare or #-prefixed hex colour, falling back when malformed."""
    if not value:
        return fallback
    value = value.lstrip("#").strip()
    if len(value) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in value):
        return "#" + value
    return fallback


def is_light(hex_color: str) -> bool:
    """
    Report whether a #rrggbb / #rgb colour reads as light.

    Uses the WCAG relative-luminance weighting rather than a plain average, so
    mid-tone greens aren't mistaken for dark backgrounds.
    """
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 0.5


def smallest_art(images: list[dict[str, Any]]) -> Optional[str]:
    """Pick the smallest album art available; thumbnails keep the SVG light."""
    if not images:
        return None
    sized = [i for i in images if i.get("width")]
    if not sized:
        return images[-1].get("url")
    return min(sized, key=lambda i: i["width"]).get("url")


def fetch_art(url: Optional[str]) -> Optional[str]:
    """Download album art and return it base64-encoded, or None on any failure."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        return b64encode(r.content).decode("ascii")
    except Exception:
        return None


def time_ago(played_at: Optional[str]) -> str:
    """Render an ISO timestamp as a short relative age (e.g. '3h', '2d')."""
    if not played_at:
        return ""
    try:
        stamp = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    seconds = (datetime.now(timezone.utc) - stamp).total_seconds()
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    if seconds < 604800:
        return f"{int(seconds // 86400)}d"
    return f"{int(seconds // 604800)}w"


def collect_tracks(count: int) -> list[dict[str, Any]]:
    """
    Fetch recent plays and reduce them to `count` distinct tracks.

    Spotify repeats a track every time it is played, so pull a wider window and
    keep only the first (most recent) appearance of each one.
    """
    data = get_recent_tracks(limit=min(50, max(20, count * 4)))
    seen: set[str] = set()
    tracks: list[dict[str, Any]] = []

    for item in data.get("items", []):
        track = item.get("track") or {}
        key = track.get("id") or track.get("name", "")
        if not key or key in seen:
            continue
        seen.add(key)
        album = track.get("album") or {}
        tracks.append(
            {
                "name": track.get("name", "Unknown"),
                "artist": ", ".join(a["name"] for a in track.get("artists", [])) or "Unknown",
                "art_url": smallest_art(album.get("images") or []),
                "ago": time_ago(item.get("played_at")),
            }
        )
        if len(tracks) == count:
            break

    with ThreadPoolExecutor(max_workers=5) as pool:
        arts = list(pool.map(lambda t: fetch_art(t["art_url"]), tracks))
    for track, art in zip(tracks, arts):
        track["art"] = art

    return tracks


def build_svg(tracks: list[dict[str, Any]], background: str, border: str, show_header: bool) -> str:
    """Assemble the finished SVG document for the given tracks."""
    text_color, muted = (
        (TEXT_ON_LIGHT, MUTED_ON_LIGHT) if is_light(background) else (TEXT_ON_DARK, MUTED_ON_DARK)
    )
    header_h = HEADER_H if show_header else 8
    height = header_h + len(tracks) * ROW_H + BOTTOM_PAD

    rows: list[str] = []
    for index, track in enumerate(tracks):
        top = header_h + index * ROW_H
        art_y = top + (ROW_H - ART) / 2
        clip = f"art{index}"

        if track.get("art"):
            art = (
                f'<clipPath id="{clip}">'
                f'<rect x="{PAD_X}" y="{art_y}" width="{ART}" height="{ART}" rx="4"/>'
                f"</clipPath>"
                f'<image x="{PAD_X}" y="{art_y}" width="{ART}" height="{ART}" '
                f'clip-path="url(#{clip})" preserveAspectRatio="xMidYMid slice" '
                f'href="data:image/jpeg;base64,{track["art"]}"/>'
            )
        else:
            art = (
                f'<rect x="{PAD_X}" y="{art_y}" width="{ART}" height="{ART}" rx="4" '
                f'fill="{muted}" fill-opacity="0.25"/>'
            )

        text_x = PAD_X + ART + 12
        rows.append(
            f"{art}"
            f'<text class="t" x="{text_x}" y="{top + 19}">'
            f"{escape_xml(truncate(track['name'], TITLE_CHARS))}</text>"
            f'<text class="a" x="{text_x}" y="{top + 34}">'
            f"{escape_xml(truncate(track['artist'], ARTIST_CHARS))}</text>"
            f'<text class="g" x="{WIDTH - PAD_X}" y="{top + 26}">'
            f"{escape_xml(track['ago'])}</text>"
        )

    header = (
        f'<text class="h" x="{PAD_X}" y="20">Recently played</text>'
        f'<circle cx="{WIDTH - PAD_X - 4}" cy="15" r="4" fill="{ACCENT}"/>'
        if show_header
        else ""
    )

    return f"""<svg viewBox="0 0 {WIDTH} {height}" preserveAspectRatio="xMidYMid meet"
 style="width: 100%; height: auto; display: block; max-width: {WIDTH}px;"
 xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
<style>
text {{ font-family: 'Segoe UI', Ubuntu, Sans-Serif; }}
.h {{ font-size: 12px; font-weight: 600; fill: {muted}; letter-spacing: .5px; }}
.t {{ font-size: 13px; font-weight: 600; fill: {text_color}; }}
.a {{ font-size: 11px; fill: {muted}; }}
.g {{ font-size: 10px; fill: {muted}; text-anchor: end; }}
</style>
<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="6"
 fill="{background}" stroke="{border}"/>
{header}
{"".join(rows)}
</svg>"""


def svg_response(markup: str, status: int = 200) -> Response:
    """Wrap SVG markup in a no-cache response so GitHub always sees fresh plays."""
    response = Response(markup, status=status, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "s-maxage=1, stale-while-revalidate"
    return response


def error_svg(message: str, status: int = 500) -> Response:
    """Render a failure as a readable card rather than an empty broken image."""
    return svg_response(
        f'<svg width="{WIDTH}" height="60" xmlns="http://www.w3.org/2000/svg">'
        f'<rect width="100%" height="100%" fill="#1a1a1a" rx="6"/>'
        f'<text x="50%" y="50%" fill="#ff6b6b" font-family="sans-serif" font-size="13" '
        f'text-anchor="middle" dominant-baseline="middle">{escape_xml(message)}</text>'
        f"</svg>",
        status,
    )


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def recent_widget(path: str) -> Response:
    """Serve the recently-played list."""
    if not is_configured():
        return error_svg("Spotify is not configured", 500)

    try:
        count = int(request.args.get("count", DEFAULT_COUNT))
    except ValueError:
        count = DEFAULT_COUNT
    count = max(1, min(MAX_COUNT, count))

    background = clean_hex(request.args.get("background_color"), "#0d1117")
    border = clean_hex(request.args.get("border_color"), "#ffffff")
    show_header = request.args.get("show_header", "true").lower() != "false"

    try:
        tracks = collect_tracks(count)
    except Exception as exc:  # surface the reason on the card itself
        return error_svg(f"Spotify error: {exc}", 502)

    if not tracks:
        return error_svg("No recently played tracks", 200)

    return svg_response(build_svg(tracks, background, border, show_header))


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=int(os.getenv("PORT", "5001")))
