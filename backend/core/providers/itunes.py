"""
The iTunes Search API (no key): album by barcode, album search, and an album
with its tracks. Covers come as artworkUrl100; the same URL with 1200x1200bb
(or larger) is the image at up to that size -- an old release's original may
be smaller, and that is what comes back.

Checked live 2026-11-22 (itunes.apple.com):
- GET /lookup?upc=<barcode>              collections (wrapperType "collection"); one
                                         barcode can give several (a box and its parts).
- GET /lookup?isrc=<isrc>                returns nothing (0 results, also with
                                         entity=song): NOT used.
- GET /search?term=..&entity=album       {results: [{collectionId, collectionName,
                                         artistName, trackCount}]}.
- GET /lookup?id=<collectionId>&entity=song  the collection, then its tracks:
                                         {trackName, trackNumber, discNumber, trackTimeMillis}.
No label (only a "copyright" line), so iTunes fills covers and the year.
Rate limit: about 20 calls a minute; we keep to one every 3.2 s.
"""
from __future__ import annotations

import urllib.parse

from core.providers.deezer import barcodes
from core.providers.http import SourceError, Throttle, get_json

NAME = "itunes"
LABEL = "iTunes"
_API = "https://itunes.apple.com"
_throttle = Throttle(3.2)
COVER_SIZE = 1200


def _get(path: str, **params) -> list[dict]:
    d = get_json(f"{_API}{path}?{urllib.parse.urlencode(params)}", _throttle)
    return (d.get("results") or []) if isinstance(d, dict) else []


def cover_url(art100: str | None, size: int = COVER_SIZE) -> str | None:
    """artworkUrl100 at `size` px (the CDN scales on request, never past the original)."""
    if not art100 or "100x100" not in art100:
        return art100 or None
    return art100.replace("100x100bb", f"{size}x{size}bb").replace("100x100", f"{size}x{size}")


def by_barcode(code: str) -> list[str]:
    """Collection ids for a barcode (usually one)."""
    for c in barcodes(code):
        got = [str(r["collectionId"]) for r in _get("/lookup", upc=c)
               if r.get("wrapperType") == "collection" and r.get("collectionId")]
        if got:
            return got
    return []


def search(album: str | None, artist: str | None, limit: int = 8) -> list[dict]:
    term = " ".join(x for x in (artist, album) if x)
    if not term:
        return []
    return [{"id": str(r["collectionId"]), "title": r.get("collectionName"), "artist": r.get("artistName"),
             "tracks": r.get("trackCount")}
            for r in _get("/search", term=term, entity="album", limit=limit) if r.get("collectionId")]


def album(collection_id: str) -> dict | None:
    rows = _get("/lookup", id=str(collection_id), entity="song")
    coll = next((r for r in rows if r.get("wrapperType") == "collection"), None)
    if not coll:
        return None
    songs = [r for r in rows if r.get("wrapperType") == "track"]
    date = (coll.get("releaseDate") or "")[:10] or None
    return {
        "source": NAME, "id": str(coll["collectionId"]), "title": coll.get("collectionName"),
        "artist": coll.get("artistName"), "barcode": None, "label": None, "date": date,
        "link": coll.get("collectionViewUrl"),
        "cover": cover_url(coll.get("artworkUrl100")), "cover_size": COVER_SIZE,
        "tracks": [{"key": f"{r.get('discNumber') or 1}-{r.get('trackNumber') or i + 1}",
                    "disc": int(r.get("discNumber") or 1), "position": int(r.get("trackNumber") or i + 1),
                    "title": r.get("trackName"),
                    "duration": r["trackTimeMillis"] / 1000 if r.get("trackTimeMillis") else None, "isrc": None}
                   for i, r in enumerate(songs)],
    }


def test() -> dict:
    try:
        got = _get("/search", term="kind of blue", entity="album", limit=1)
    except SourceError as e:
        return {"ok": False, "message": f"iTunes: {e}"}
    return ({"ok": True, "message": "iTunes answered (no key needed)"} if got
            else {"ok": False, "message": "iTunes answered, but with no results"})
