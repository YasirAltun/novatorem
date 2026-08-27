"""
JSON playback feed for pages that can render their own markup.

The SVG strips exist because a GitHub README can't run scripts and blocks
external images inside an SVG, which forces every album cover to be inlined as
base64 — tens of kilobytes that no browser cache can reuse. A real web page has
neither limit, so it gets the data instead: a few hundred bytes, with album art
as ordinary CDN URLs the browser caches on its own.

Carrying `progress_ms` alongside `duration_ms` lets the client draw a moving
progress bar between polls and schedule its next request for when the track
actually ends, rather than polling blindly.
"""

import json
import os
import time
from typing import Any

from flask import Flask, Response, request

from .playback import now_playing, recent_list
from .spotify import is_configured

app = Flask(__name__)

DEFAULT_RECENT = 5
MAX_RECENT = 10

# Far shorter than the SVG's 60s: the payload is tiny, and this is the surface
# where being a minute behind would actually show. `stale-if-error` matters
# more than it looks — if Spotify rate-limits or falls over, the edge keeps
# serving the last good answer instead of every visitor seeing an error.
FEED_CACHE = (
    "public, max-age=0, s-maxage=4, stale-while-revalidate=8, stale-if-error=600"
)


def json_response(payload: dict[str, Any], status: int = 200) -> Response:
    """Serialise a payload with the caching and CORS headers the feed needs."""
    response = Response(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        status=status,
        mimetype="application/json; charset=utf-8",
    )
    response.headers["Cache-Control"] = FEED_CACHE
    response.headers["CDN-Cache-Control"] = FEED_CACHE
    # Read-only public data, and the page lives on another origin.
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


@app.route("/", defaults={"path": ""}, methods=["GET", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "OPTIONS"])
def feed(path: str) -> Response:
    """
    Return the current track, and the recent history when asked for it.

    History costs a second Spotify call but only changes when a track ends, so
    clients poll with `count=0` and ask for it again only when the current
    track changes. That keeps the hot path at one upstream call, which is what
    keeps us clear of Spotify's rate limit when several edge locations are
    refreshing at once.
    """
    if request.method == "OPTIONS":
        return json_response({})

    if not is_configured():
        return json_response({"error": "not_configured"}, 500)

    try:
        count = int(request.args.get("count", DEFAULT_RECENT))
    except ValueError:
        count = DEFAULT_RECENT
    count = max(0, min(MAX_RECENT, count))

    try:
        now = now_playing()
        recent = recent_list(count) if count else []
    except Exception as exc:
        return json_response({"error": "spotify", "detail": str(exc)}, 502)

    # The client needs to know how stale `progress_ms` already was when it
    # arrived, since an edge-cached response can be seconds old.
    return json_response(
        {
            "now": now,
            "recent": [t for t in recent if t.get("id") != now.get("id")][: max(0, count - 1)]
            if count
            else [],
            "served_at": int(time.time() * 1000),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=int(os.getenv("PORT", "5003")))
