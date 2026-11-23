"""
Album sources (Settings → Album sources): Deezer and iTunes, for covers and
-- only where the tag is empty -- the label and the year.

match_album() finds a folder's album on each source (core/sourcematch.py's
ladder) and records it (core/sourcelinks.py); covers() lists the cover each
linked source has, in the cover order; fill() stages the label / year the
CONFIRMED links give, for tracks where that tag is empty. Nothing here holds
a database connection while a service is being asked.
"""
from __future__ import annotations

from collections import Counter
from core import albummatch, sourcelinks, sourcematch, staging
from core.database import db
from core.providers import deezer, itunes
from core.providers.http import SourceError

PROVIDERS = {"deezer": deezer, "itunes": itunes}
LABELS = {"caa": "Cover Art Archive", "itunes": "iTunes", "deezer": "Deezer", "discogs": "Discogs"}
FILL_FIELDS = ("label", "year")

_COLS = ("id, path, directory, filename, title, artist, album, album_artist, year, track_number, disc_number,"
         " duration, barcode, isrc, mb_album_id, label")


def enabled(cfg) -> list[str]:
    return [s for s in PROVIDERS if getattr(cfg, f"source_{s}", False)]


def allowed(cfg, source: str, field: str) -> bool:
    return field in (cfg.source_fields.get(source) or [])


def rows_for(conn, directory: str) -> list[dict]:
    return [dict(r) for r in conn.execute(f"SELECT {_COLS} FROM tracks WHERE directory = ?"
                                          " ORDER BY CAST(disc_number AS INTEGER), CAST(track_number AS INTEGER),"
                                          " filename", (directory,))]


def _common(rows: list[dict], field: str) -> str | None:
    vals = Counter(v for v in (r.get(field) for r in rows) if v and not albummatch.is_placeholder(v))
    return vals.most_common(1)[0][0] if vals else None


def evidence(rows: list[dict], mb_barcode=None) -> dict:
    """What a folder offers to find it by: album and artist names, barcode, ISRCs."""
    album, artist = _common(rows, "album"), _common(rows, "album_artist") or _common(rows, "artist")
    if rows and not (album and artist):
        p_album, p_artist = albummatch.names_from_path(rows[0]["path"], None)
        album, artist = album or p_album, artist or p_artist
    barcode = _common(rows, "barcode")
    if not barcode and mb_barcode:
        mb = _common(rows, "mb_album_id")
        barcode = mb_barcode(mb) if mb else None
    return {"album": album, "artist": artist, "barcode": barcode,
            "isrcs": [r["isrc"] for r in rows if r.get("isrc")]}


def _mb_barcode(mb_album_id: str) -> str | None:
    from core.providers import musicbrainz as mbp
    rel = mbp.get_release(mb_album_id)
    return (rel or {}).get("barcode")


def match_album(directory: str, cfg, sources: list[str] | None = None, refresh: bool = False) -> dict:
    """
    The folder's album on each source: kept links as they are (unless
    `refresh`), else looked up. A barcode / ISRC match is linked and confirmed;
    the best search match is linked unconfirmed (or confirmed, with the
    "auto" policy at or above its threshold); nothing found is recorded.
    """
    sources = [s for s in (sources or enabled(cfg)) if s in PROVIDERS]
    with db() as conn:
        rows = rows_for(conn, directory)
        links = sourcelinks.get(conn, directory)
    out = {"directory": directory, "links": {}, "candidates": {}, "errors": {}}
    if not rows:
        out["errors"]["*"] = "no tracks in that folder"
        return out
    todo = [s for s in sources if refresh or s not in links]
    out["links"] = {s: links[s] for s in sources if s in links and s not in todo}
    ev = evidence(rows, _mb_barcode if todo else None)
    out.update(album=ev["album"], artist=ev["artist"], barcode=ev["barcode"])
    for src in todo:
        try:
            found = sourcematch.find(rows, PROVIDERS[src], album=ev["album"], artist=ev["artist"],
                                     barcode=ev["barcode"], isrcs=ev["isrcs"])
        except SourceError as e:
            out["errors"][src] = str(e)
            if src in links:
                out["links"][src] = links[src]
            continue
        with db() as conn:
            if found["link"]:
                link = sourcelinks.save_link(conn, directory, found["link"], found["method"], True)
            elif found["candidates"]:
                best = found["candidates"][0]
                auto = cfg.source_search_policy == "auto" and best["score"] >= cfg.source_auto_threshold
                link = sourcelinks.save_link(conn, directory, best, "search", auto)
            else:
                sourcelinks.save_missing(conn, directory, src)
                link = sourcelinks.get(conn, directory)[src]
        out["links"][src] = link
        out["candidates"][src] = found["candidates"]
    return out


def link_manual(directory: str, source: str, album_id: str) -> dict:
    """Link the folder to an album you picked (or pasted): confirmed."""
    prov = PROVIDERS[source]
    with db() as conn:
        rows = rows_for(conn, directory)
    if not rows:
        raise ValueError("no tracks in that folder")
    a = prov.album(album_id)
    if not a:
        raise LookupError(f"no {LABELS[source]} album {album_id}")
    cand = sourcematch.candidate(a, sourcematch.score(rows, a, "picked by you"))
    with db() as conn:
        return sourcelinks.save_link(conn, directory, cand, "manual", True)


def covers(directory: str, cfg, discogs_token: str = "") -> dict:
    """
    {order, covers}: each source's cover for the folder, in the cover order
    (caa stays in `order` for the page, which reads the Cover Art Archive
    itself). Sources not linked yet are looked up first.
    """
    order = [s for s in cfg.cover_order if s in LABELS]
    want = [s for s in order if s in enabled(cfg) and allowed(cfg, s, "cover")]
    m = match_album(directory, cfg, want) if want else {"links": {}, "errors": {}, "album": None, "artist": None}
    out = []
    for s in order:
        link = m["links"].get(s)
        if s in want and link and link["status"] == "linked" and link.get("cover"):
            out.append({"source": s, "label": LABELS[s], "url": link["cover"], "size": link.get("cover_size"),
                        "confirmed": link["confirmed"], "title": link.get("title"), "artist": link.get("artist"),
                        "reasons": link.get("reasons"), "method": link.get("method")})
        elif s == "discogs" and discogs_token:
            hit = _discogs_cover(discogs_token, m.get("album"), m.get("artist"))
            if hit:
                out.append(hit)
    return {"order": order, "covers": out, "errors": m.get("errors", {})}


def _discogs_cover(token: str, album: str | None, artist: str | None) -> dict | None:
    """Discogs's first release hit with an image: a name match only, so never confirmed."""
    if not (album or artist):
        return None
    from core.providers.discogs import DiscogsProvider
    for r in DiscogsProvider(token)._search_sync(None, artist, album):
        img = (r.raw or {}).get("cover_image")
        if img and "spacer.gif" not in img and sourcematch.overlap(album, r.album) >= sourcematch.NAME_MIN:
            return {"source": "discogs", "label": "Discogs", "url": img, "size": None, "confirmed": False,
                    "title": r.album, "artist": r.artist, "reasons": "name match only", "method": "search"}
    return None


def _fill_values(links: dict, cfg) -> dict[str, tuple[str, str]]:
    """{field: (value, source)} from confirmed links, the first source in the cover order that has it."""
    order = [s for s in cfg.cover_order if s in PROVIDERS and s in enabled(cfg)]
    out: dict[str, tuple[str, str]] = {}
    for field in FILL_FIELDS:
        for s in order:
            link = links.get(s)
            if not (link and link["status"] == "linked" and link["confirmed"] and allowed(cfg, s, field)):
                continue
            v = link.get("label") if field == "label" else (link.get("date") or "")[:4]
            if v and (field != "year" or v.isdigit()):
                out[field] = (v, s)
                break
    return out


def _file_label(path: str) -> str | None:
    """The label as the file has it now (the database may not have read it yet)."""
    from core.tagger import read_tags
    try:
        return read_tags(path, extended=False).get("label")
    except Exception:  # noqa: BLE001 -- unreadable: treated as having one, so nothing is written
        return "?"


def fill(conn, track_ids: list[int], cfg, preview: bool = True) -> dict:
    """
    The label / year the folders' confirmed links give, for the tracks where
    that tag is empty (as staged). preview: just the plan; else staged as one step.
    """
    from api.tags import _finish, _rows
    rows = _rows(conn, track_ids)
    entries = staging.load(conn, [r["id"] for r in rows])
    by_dir: dict[str, list] = {}
    for r in rows:
        by_dir.setdefault(r["directory"], []).append(r)
    links = sourcelinks.get_many(conn, list(by_dir))
    albums, todo = [], []
    for d, rs in by_dir.items():
        vals = _fill_values(links.get(d, {}), cfg)
        changes = []
        for r in rs:
            entry = entries.get(r["id"])
            eff = staging.effective(r, entry)
            tags = {}
            if "year" in vals and not (eff.get("year") or "").strip():
                tags["year"] = vals["year"][0]
            if "label" in vals:
                staged = entry and "label" in entry["tags"]
                cur = eff.get("label") if staged else (r["label"] or _file_label(r["path"]))
                if not (cur or "").strip():
                    tags["label"] = vals["label"][0]
            if tags:
                changes.append((r, tags))
        todo += changes
        albums.append({"directory": d, "values": {f: {"value": v, "source": LABELS[s]} for f, (v, s) in vals.items()},
                       "tracks": len(rs), "changes": len(changes),
                       "fields": sorted({f for _, t in changes for f in t})})
    out = {"albums": albums, "changes": len(todo)}
    if preview or not todo:
        return {**out, "staged": False}
    fields = sorted({f for _, t in todo for f in t})
    step = staging.Step(conn, f"Fill {' and '.join(fields)} from album sources on {len(todo)} tracks")
    for r, tags in todo:
        staging.stage_edit(conn, r, tags, step)
    return {**out, **_finish(conn, step, [r["id"] for r, _ in todo])}


def folder_of(conn, track_ids: list[int]) -> str:
    """The one album folder a selection is in (ValueError when it spans several)."""
    from api.tags import _rows
    dirs = {r["directory"] for r in _rows(conn, track_ids)}
    if len(dirs) != 1:
        raise ValueError("select the tracks of one album folder")
    return next(iter(dirs))
