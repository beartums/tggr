"""
"Not on MusicBrainz": tracks whose album was looked up and nothing right was
found -- a search with no releases, or marked by hand (api/lookup.py). Shapes
the No MusicBrainz album check (its view settings, api/config.py mb_view_*)
and the album list's Not found column.
"""
from __future__ import annotations

import time


def record(conn, track_ids: list[int], status: str = "missing") -> None:
    now = time.time()
    conn.executemany("INSERT INTO mb_lookups (track_id, status, at) VALUES (?, ?, ?) "
                     "ON CONFLICT(track_id) DO UPDATE SET status = excluded.status, at = excluded.at",
                     [(i, status, now) for i in track_ids])


def not_found_sql(before: float | None = None) -> str:
    when = f" AND at < {float(before)}" if before is not None else ""
    return f"tracks.id IN (SELECT track_id FROM mb_lookups WHERE status = 'missing'{when})"


def no_album_sql(base: str, not_found: bool = True, before: float | None = None) -> str:
    """The No MusicBrainz album check: not-found tracks in, out, or only those last tried before `before`."""
    if not not_found:
        return f"({base} AND NOT {not_found_sql()})"
    if before is not None:
        return (f"({base} AND tracks.id NOT IN (SELECT track_id FROM mb_lookups"
                f" WHERE status = 'missing' AND at >= {float(before)}))")
    return base
