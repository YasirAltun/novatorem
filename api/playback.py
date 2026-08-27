"""
Normalised playback state, shared by the SVG strips and the JSON feed.

Sits between the raw Spotify endpoints and the renderers so every surface reads
the same shape. Two deliberate differences from `spotify.get_now_playing`:

* when nothing is playing this falls back to the *most recent* track rather
  than a random pick from the last ten, which made the widget look like it was
  showing the wrong song on every reload;
* it carries `progress_ms` / `duration_ms`, so a client can draw a progress bar
  and schedule its next poll for when the track actually ends.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from .config import spotify_config
from .exceptions import NoTracksError
from .spotify import _api_get, get_recent_tracks


def smallest_art(images: list[dict[str, Any]]) -> Optional[str]:
    """
    Pick the smallest album art on offer.

    Spotify returns 640/300/64px variants; the strips draw it at 32px, so the
    smallest is both enough and a fraction of the bytes.
    """
    if not images:
        return None
    sized = [i for i in images if i.get("width")]
    if not sized:
        return images[-1].get("url")
    return min(sized, key=lambda i: i["width"]).get("url")


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


def _track_fields(track: dict[str, Any]) -> dict[str, Any]:
    """Reduce a Spotify track object to the fields every surface needs."""
    album = track.get("album") or {}
    return {
        "id": track.get("id") or "",
        "track": track.get("name", "Unknown"),
        "artist": ", ".join(a["name"] for a in track.get("artists", [])) or "Unknown",
        "art": smallest_art(album.get("images") or []),
        "url": (track.get("external_urls") or {}).get("spotify", ""),
        "duration_ms": track.get("duration_ms") or 0,
    }


def now_playing() -> dict[str, Any]:
    """
    What's on right now, or the most recent play when nothing is.

    Adds `playing` and `progress_ms` to the common track fields, plus `ago`
    when the track comes from history rather than the player.
    """
    try:
        data = _api_get(spotify_config.now_playing_url)
    except NoTracksError:
        data = None

    item = (data or {}).get("item")
    if item:
        info = _track_fields(item)
        info["playing"] = bool(data.get("is_playing"))
        info["progress_ms"] = data.get("progress_ms") or 0
        return info

    history = (get_recent_tracks(limit=1).get("items") or [])
    if not history:
        raise NoTracksError("Spotify")

    info = _track_fields(history[0].get("track") or {})
    info["playing"] = False
    info["progress_ms"] = 0
    info["ago"] = time_ago(history[0].get("played_at"))
    return info


def recent_list(count: int) -> list[dict[str, Any]]:
    """
    The last `count` distinct tracks, most recent first.

    Spotify repeats a track for every play, so this pulls a wider window and
    keeps only the first appearance of each one.
    """
    data = get_recent_tracks(limit=min(50, max(20, count * 4)))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for item in data.get("items", []):
        track = item.get("track") or {}
        key = track.get("id") or track.get("name", "")
        if not key or key in seen:
            continue
        seen.add(key)
        info = _track_fields(track)
        info["ago"] = time_ago(item.get("played_at"))
        out.append(info)
        if len(out) == count:
            break

    return out
