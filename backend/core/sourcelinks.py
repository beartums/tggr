"""
source_links: each album folder's album on a cover/album source (Deezer,
iTunes), so a match is made once and reused (covers, label and year fills).

  status linked   album_id is the album; confirmed = a barcode / ISRC named it,
                  you picked or confirmed it, or a search match was accepted
                  automatically (Settings → Album sources). An unconfirmed link
                  is a search match waiting for you: never used to fill tags.
  status missing  looked up, nothing right found (at = when). Shapes the No
                  cover source check (its ⚙: not-found in or out, or only those
                  last tried before a day), like mb_lookups.
"""
from __future__ import annotations

import json
import time

METHODS = ("barcode", "isrc", "search", "manual")


def save_link(conn, directory: str, cand: dict, method: str, confirmed: bool) -> dict:
    if method not in METHODS:
        raise ValueError(f"method: one of {METHODS}")
    info = {k: cand.get(k) for k in ("title", "artist", "date", "label", "barcode", "link", "cover",
                                     "cover_size", "tracks")}
    row = (directory, cand["source"], cand["id"], method, cand.get("score"), cand.get("reasons"),
           int(confirmed), "linked", json.dumps(info), time.time())
    conn.execute("INSERT INTO source_links (directory, source, album_id, method, score, reasons, confirmed,"
                 " status, info, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                 " ON CONFLICT(directory, source) DO UPDATE SET album_id = excluded.album_id,"
                 " method = excluded.method, score = excluded.score, reasons = excluded.reasons,"
                 " confirmed = excluded.confirmed, status = excluded.status, info = excluded.info,"
                 " at = excluded.at", row)
    return get(conn, directory)[cand["source"]]


def save_missing(conn, directory: str, source: str) -> None:
    conn.execute("INSERT INTO source_links (directory, source, status, confirmed, at) VALUES (?, ?, 'missing', 0, ?)"
                 " ON CONFLICT(directory, source) DO UPDATE SET album_id = NULL, method = NULL, score = NULL,"
                 " reasons = NULL, info = NULL, confirmed = 0, status = 'missing', at = excluded.at",
                 (directory, source, time.time()))


def confirm(conn, directory: str, source: str) -> bool:
    return conn.execute("UPDATE source_links SET confirmed = 1 WHERE directory = ? AND source = ?"
                        " AND status = 'linked'", (directory, source)).rowcount > 0


def unlink(conn, directory: str, source: str) -> bool:
    return conn.execute("DELETE FROM source_links WHERE directory = ? AND source = ?",
                        (directory, source)).rowcount > 0


def _shape(r) -> dict:
    return {"directory": r["directory"], "source": r["source"], "status": r["status"], "album_id": r["album_id"],
            "method": r["method"], "score": r["score"], "reasons": r["reasons"],
            "confirmed": bool(r["confirmed"]), "at": r["at"], **(json.loads(r["info"]) if r["info"] else {})}


def get(conn, directory: str) -> dict[str, dict]:
    """{source: link} for one album folder."""
    return {r["source"]: _shape(r) for r in
            conn.execute("SELECT * FROM source_links WHERE directory = ?", (directory,))}


def get_many(conn, directories: list[str]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for r in conn.execute("SELECT * FROM source_links WHERE directory IN (SELECT value FROM json_each(?))",
                          (json.dumps(sorted(set(directories))),)):
        out.setdefault(r["directory"], {})[r["source"]] = _shape(r)
    return out


# ─── Quality checks (api/library.py) ────────────────────────────────────────

UNCONFIRMED_SQL = ("directory IN (SELECT directory FROM source_links"
                   " WHERE status = 'linked' AND confirmed = 0)")
_LINKED = "directory IN (SELECT directory FROM source_links WHERE status = 'linked')"


def not_found_sql(before: float | None = None) -> str:
    when = f" AND at < {float(before)}" if before is not None else ""
    return f"directory IN (SELECT directory FROM source_links WHERE status = 'missing'{when})"


def no_source_sql(no_cover: str, not_found: bool = True, before: float | None = None) -> str:
    """
    No cover source: no cover art, and no source has the album linked. Albums
    looked up with nothing found: all in, out, or only those last tried before `before`.
    """
    base = f"({no_cover} AND NOT {_LINKED})"
    if not not_found:
        return f"({base} AND NOT {not_found_sql()})"
    if before is not None:
        return (f"({base} AND directory NOT IN (SELECT directory FROM source_links"
                f" WHERE status = 'missing' AND at >= {float(before)}))")
    return base
