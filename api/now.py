"""
Single-line now-playing strip.

Same 540x62 anatomy as the recent-tracks ticker in recent.py (art, title,
artist, transparent-capable surface), so the two sit side by side at identical
heights. The right edge carries four EQ bars that animate while a track is
actually playing and sit dimmed when the shown track is a recent play.
"""

import os
from dataclasses import asdict, is_dataclass
from typing import Any

from flask import Flask, Response, request

from .recent import (
    ACCENT,
    ART,
    ARTIST_CHARS,
    BOTTOM_PAD,
    PAD_X,
    ROW_H,
    TITLE_CHARS,
    WIDTH,
    base_styles,
    card_rect,
    clean_hex,
    error_svg,
    escape_xml,
    fetch_art,
    pick_palette,
    svg_response,
    truncate,
)
from .spotify import get_now_playing, is_configured

app = Flask(__name__)

TOP = 8  # headerless top padding, matching the ticker's show_header=false
HEIGHT = TOP + ROW_H + BOTTOM_PAD  # 62, identical to the single-line ticker

# The EQ indicator: four bars, slightly desynced so the motion reads as audio.
EQ_BARS = ((0.9, 0.00), (0.7, 0.20), (1.1, 0.45), (0.8, 0.10))
EQ_W, EQ_GAP, EQ_H = 4, 2, 16


def build_now_svg(
    track: dict[str, Any], background: str, border: str, palette: tuple[str, str]
) -> str:
    """Assemble the strip for the given normalized track."""
    text_color, muted = palette
    playing = bool(track.get("is_playing"))
    art_y = TOP + (ROW_H - ART) / 2

    art_b64 = fetch_art(track.get("album_art_url") or None)
    if art_b64:
        art = (
            '<clipPath id="nowart">'
            f'<rect x="{PAD_X}" y="{art_y}" width="{ART}" height="{ART}" rx="4"/>'
            "</clipPath>"
            f'<image x="{PAD_X}" y="{art_y}" width="{ART}" height="{ART}" '
            'clip-path="url(#nowart)" preserveAspectRatio="xMidYMid slice" '
            f'href="data:image/jpeg;base64,{art_b64}"/>'
        )
    else:
        art = (
            f'<rect x="{PAD_X}" y="{art_y}" width="{ART}" height="{ART}" rx="4" '
            f'fill="{muted}" fill-opacity="0.25"/>'
        )

    eq_x = WIDTH - PAD_X - (len(EQ_BARS) * (EQ_W + EQ_GAP) - EQ_GAP)
    eq_y = TOP + (ROW_H - EQ_H) / 2
    bars = "".join(
        f'<rect class="eq eq{i}" x="{eq_x + i * (EQ_W + EQ_GAP)}" y="{eq_y}" '
        f'width="{EQ_W}" height="{EQ_H}" rx="1"/>'
        for i in range(len(EQ_BARS))
    )

    if playing:
        eq_css = "\n".join(
            [
                f".eq {{ fill: {ACCENT}; transform-box: fill-box; transform-origin: 50% 100%; "
                "animation: eqp 0.9s ease-in-out infinite alternate; }"
            ]
            + [
                f".eq{i} {{ animation-duration: {dur}s; animation-delay: -{delay}s; }}"
                for i, (dur, delay) in enumerate(EQ_BARS)
            ]
            + [
                "@keyframes eqp { from { transform: scaleY(0.25); } to { transform: scaleY(1); } }",
                "@media (prefers-reduced-motion: reduce) { .eq { animation: none; } }",
            ]
        )
    else:
        eq_css = f".eq {{ fill: {muted}; opacity: 0.35; }}"

    text_x = PAD_X + ART + 12
    return f"""<svg viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="xMidYMid meet"
 style="width: 100%; height: auto; display: block; max-width: {WIDTH}px;"
 xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
<style>
{base_styles(text_color, muted)}
{eq_css}
</style>
{card_rect(background, border, HEIGHT)}
{art}
<text class="t" x="{text_x}" y="{TOP + 19}">{escape_xml(truncate(track.get("track_name", "Unknown"), TITLE_CHARS))}</text>
<text class="a" x="{text_x}" y="{TOP + 34}">{escape_xml(truncate(track.get("artist_name", "Unknown"), ARTIST_CHARS))}</text>
{bars}
</svg>"""


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def now_widget(path: str) -> Response:
    """Serve the single-line now-playing strip."""
    if not is_configured():
        return error_svg("Spotify is not configured", 500)

    background = clean_hex(request.args.get("background_color"), "#0d1117")
    border = clean_hex(request.args.get("border_color"), "#ffffff")
    palette = pick_palette(background, request.args.get("theme"))

    try:
        track = get_now_playing()
    except Exception as exc:  # surface the reason on the card itself
        return error_svg(f"Spotify error: {exc}", 502)

    if is_dataclass(track):
        track = asdict(track)
    if not isinstance(track, dict):
        return error_svg("Unexpected Spotify payload", 502)

    return svg_response(build_now_svg(track, background, border, palette))


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=int(os.getenv("PORT", "5002")))
