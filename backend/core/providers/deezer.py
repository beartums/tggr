"""
Deezer's public API (no key): album by barcode, track by ISRC, album search,
and an album with its tracks, label, release date and a 1000 px cover.

Checked live 2026-11-22 (api.deezer.com):
- GET /album/upc:<barcode>    the album, or {"error": {"code": 800, "message": "no data"}}.
                              The barcode must be as Deezer stores it (12-digit UPC
                              matched; the same with a leading 0 did not), so callers
                              try a few spellings (barcodes()).
- GET /track/isrc:<isrc>      the track, with album.id.
- GET /search/album?q=...     {data: [{id, title, artist{name}, nb_tracks, cover_xl}], total}.
                              artist:"" album:"" narrow the search but don't filter strictly.
- GET /album/<id>             {id, title, upc, label, release_date, nb_tracks, duration,
                              cover_xl (1000x1000), link, artist{name}}.
- GET /album/<id>/tracks      {data: [{title, title_short, duration (s), track_position,
                              disk_number, isrc}], next}. The tracks embedded in /album/<id>
                              have no positions, so this is the one to read.
Quota: 50 requests per 5 seconds per IP; we keep to ~8 a second.
"""
from __future__ import annotations

import urllib.parse

from core.providers.http import SourceError, Throttle, get_json

NAME = "deezer"
LABEL = "Deezer"
_API = "https://api.deezer.com"
_throttle = Throttle(0.13)


def _get(path: str) -> dict | None:
    d = get_json(_API + path, _throttle)
    if not isinstance(d, dict):
        return None
    err = d.get("error")
    if err:
        if isinstance(err, dict) and err.get("code") == 800:      # "no data"
            return None
        raise SourceError(f"Deezer: {err.get('message') if isinstance(err, dict) else err}")
    return d


def barcodes(code: str) -> list[str]:
    """The spellings of a barcode worth trying: as tagged, without leading zeros, as 12 and 13 digits."""
    digits = "".join(c for c in code or "" if c.isdigit())
    if not digits:
        return []
    out = [digits, digits.lstrip("0"), digits.lstrip("0").zfill(12), digits.lstrip("0").zfill(13)]
    return [c for i, c in enumerate(out) if c and c not in out[:i]]


def by_barcode(code: str) -> list[str]:
    """The Deezer album id for a barcode ([] when none)."""
    for c in barcodes(code):
        d = _get(f"/album/upc:{c}")
        if d and d.get("id"):
            return [str(d["id"])]
    return []


def album_by_isrc(isrc: str) -> str | None:
    d = _get(f"/track/isrc:{urllib.parse.quote(isrc.strip())}")
    return str(d["album"]["id"]) if d and (d.get("album") or {}).get("id") else None


def search(album: str | None, artist: str | None, limit: int = 8) -> list[dict]:
    """Album hits: {id, title, artist, tracks}; best first as Deezer ranks them."""
    parts = [f'artist:"{artist}"' if artist else "", f'album:"{album}"' if album else ""]
    q = " ".join(p for p in parts if p)
    if not q:
        return []
    d = _get("/search/album?" + urllib.parse.urlencode({"q": q, "limit": limit}))
    hits = (d or {}).get("data") or []
    if not hits and album and artist:          # the strict form found nothing: plain words
        d = _get("/search/album?" + urllib.parse.urlencode({"q": f"{artist} {album}", "limit": limit}))
        hits = (d or {}).get("data") or []
    return [{"id": str(h["id"]), "title": h.get("title"), "artist": (h.get("artist") or {}).get("name"),
             "tracks": h.get("nb_tracks")} for h in hits if h.get("id")]


def album(album_id: str) -> dict | None:
    """The album in tggr's shape (sourcematch.Album), tracks included."""
    a = _get(f"/album/{urllib.parse.quote(str(album_id))}")
    if not a:
        return None
    tracks, url = [], f"/album/{a['id']}/tracks?limit=100"
    while url and len(tracks) < 400:
        page = _get(url) or {}
        tracks += page.get("data") or []
        nxt = page.get("next")
        url = nxt[len(_API):] if nxt and nxt.startswith(_API) else None
    date = a.get("release_date") or ""
    return {
        "source": NAME, "id": str(a["id"]), "title": a.get("title"),
        "artist": (a.get("artist") or {}).get("name"),
        "barcode": a.get("upc") or None, "label": a.get("label") or None,
        "date": date if date and not date.startswith("0000") else None,
        "link": a.get("link") or f"https://www.deezer.com/album/{a['id']}",
        "cover": a.get("cover_xl") or a.get("cover_big") or None, "cover_size": 1000 if a.get("cover_xl") else 500,
        "tracks": [{"key": f"{t.get('disk_number') or 1}-{t.get('track_position') or i + 1}",
                    "disc": int(t.get("disk_number") or 1), "position": int(t.get("track_position") or i + 1),
                    # title_short drops "(feat. ...)", which would sink the title match.
                    "title": t.get("title_short") or t.get("title"),
                    "duration": float(t["duration"]) if t.get("duration") else None,
                    "isrc": t.get("isrc") or None}
                   for i, t in enumerate(tracks)],
    }


def test() -> dict:
    try:
        d = _get("/search/album?" + urllib.parse.urlencode({"q": "kind of blue", "limit": 1}))
    except SourceError as e:
        return {"ok": False, "message": f"Deezer: {e}"}
    return ({"ok": True, "message": "Deezer answered (no key needed)"} if d and d.get("data")
            else {"ok": False, "message": "Deezer answered, but with no results"})
