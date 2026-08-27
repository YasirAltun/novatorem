"""
Single-line now-playing strip.

Same 540x62 anatomy as the recent-tracks ticker in recent.py (art, title,
artist, transparent-capable surface), so the two sit side by side at identical
heights. The right half carries a waveform band: mirrored bars that dance while
a track is playing and hold a frozen wave otherwise (`wave` param overrides).
"""

import os
from dataclasses import asdict, is_dataclass
from typing import Any

from flask import Flask, Response, request

from .recent import (
    ACCENT,
    ART,
    BOTTOM_PAD,
    PAD_X,
    ROW_H,
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

# Waveform band on the right: mirrored bars around the row's vertical centre.
WAVE_W = 3
WAVE_GAP = 3
# Hand-tuned height profile (tiled over the bars) so the frozen wave looks
# like audio rather than noise.
WAVE_PROFILE = (10, 16, 22, 14, 8, 18, 26, 20, 12, 24, 16, 9, 21)


def wave_metrics(width: int) -> tuple[int, int]:
    """Bar count and starting x for the band: roughly 30% of the strip width."""
    bars = max(12, int(width * 0.3) // (WAVE_W + WAVE_GAP))
    x0 = width - PAD_X - (bars * (WAVE_W + WAVE_GAP) - WAVE_GAP)
    return bars, x0


def wave_band(animated: bool, muted: str, width: int) -> tuple[str, str]:
    """Build the waveform rects and their CSS. Returns (markup, css)."""
    bars_n, x0 = wave_metrics(width)
    mid = TOP + ROW_H / 2
    rects = []
    for i in range(bars_n):
        h = WAVE_PROFILE[i % len(WAVE_PROFILE)]
        x = x0 + i * (WAVE_W + WAVE_GAP)
        # Deterministic per-bar timing so the dance is desynced but stable.
        dur = 0.6 + ((i * 37) % 40) / 100  # 0.60s - 0.99s
        delay = ((i * 53) % 70) / 100  # 0.00s - 0.69s
        style = f' style="animation-duration:{dur:.2f}s;animation-delay:-{delay:.2f}s"' if animated else ""
        rects.append(
            f'<rect class="w" x="{x}" y="{mid - h / 2}" width="{WAVE_W}" height="{h}" rx="1.5"{style}/>'
        )

    if animated:
        css = (
            f".w {{ fill: {ACCENT}; transform-box: fill-box; transform-origin: 50% 50%; "
            "animation: wavep 0.8s ease-in-out infinite alternate; }\n"
            "@keyframes wavep { from { transform: scaleY(0.3); } to { transform: scaleY(1); } }\n"
            "@media (prefers-reduced-motion: reduce) { .w { animation: none; } }"
        )
    else:
        css = f".w {{ fill: {muted}; opacity: 0.4; }}"
    return "".join(rects), css


def build_now_svg(
    track: dict[str, Any],
    background: str,
    border: str,
    palette: tuple[str, str],
    wave: str,
    width: int = WIDTH,
) -> str:
    """Assemble the strip for the given normalized track."""
    text_color, muted = palette
    playing = bool(track.get("is_playing"))
    art_y = TOP + (ROW_H - ART) / 2

    # Text must stop before the wave starts (or the strip edge when wave=off).
    text_x = PAD_X + ART + 12
    text_end = wave_metrics(width)[1] - 10 if wave != "off" else width - PAD_X
    title_chars = max(12, int((text_end - text_x) / 7.2))
    artist_chars = max(14, int((text_end - text_x) / 5.9))

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

    if wave == "off":
        bars, wave_css = "", ""
    else:
        animated = playing if wave == "auto" else True
        bars, wave_css = wave_band(animated, muted, width)

    return f"""<svg viewBox="0 0 {width} {HEIGHT}" preserveAspectRatio="xMidYMid meet"
 style="width: 100%; height: auto; display: block; max-width: {width}px;"
 xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
<style>
{base_styles(text_color, muted)}
{wave_css}
</style>
{card_rect(background, border, HEIGHT, width)}
{art}
<text class="t" x="{text_x}" y="{TOP + 19}">{escape_xml(truncate(track.get("track_name", "Unknown"), title_chars))}</text>
<text class="a" x="{text_x}" y="{TOP + 34}">{escape_xml(truncate(track.get("artist_name", "Unknown"), artist_chars))}</text>
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

    wave = request.args.get("wave", "auto").lower()
    if wave not in ("auto", "on", "off"):
        wave = "auto"

    try:
        width = int(request.args.get("width", WIDTH))
    except ValueError:
        width = WIDTH
    width = max(300, min(WIDTH, width))

    try:
        track = get_now_playing()
    except Exception as exc:  # surface the reason on the card itself
        return error_svg(f"Spotify error: {exc}", 502)

    if is_dataclass(track):
        track = asdict(track)
    if not isinstance(track, dict):
        return error_svg("Unexpected Spotify payload", 502)

    return svg_response(build_now_svg(track, background, border, palette, wave, width))


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=int(os.getenv("PORT", "5002")))
