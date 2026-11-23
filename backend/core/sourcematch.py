"""
Which album on Deezer / iTunes is this folder? The strongest evidence first:

  1. barcode   the tracks' BARCODE tag (a CD rip records it), else the matched
               MusicBrainz release's: one product, so the link is made outright.
  2. ISRC      the tracks' ISRCs (Deezer only; iTunes can't look them up): when
               most of them land on one album, the link is made outright.
  3. search    artist + album text search, each hit scored on its TRACKS --
               titles paired as Match album pairs them (core/albummatch.py, the
               same title gate), then track count, length, year as a tiebreak.
               A hit whose titles don't agree is never proposed: a matching
               artist and album name says nothing about which edition it is.

Each candidate carries the reasons for its score ("12/12 titles · length ±4 s
· barcode"). Standard library only; the providers are passed in, so the
ladder runs against fakes in tests.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Protocol

from core import albummatch

ISRC_SAMPLE = 5          # ISRCs looked up per album (each is one call)
SEARCH_PROBES = 3        # search hits read in full (each is one or more calls)
NAME_MIN = 0.5           # a hit's album name must share this much with ours to be read at all


class Provider(Protocol):
    NAME: str

    def by_barcode(self, code: str) -> list[str]: ...
    def search(self, album: str | None, artist: str | None) -> list[dict]: ...
    def album(self, album_id: str) -> dict | None: ...


def _words(s: str | None) -> set[str]:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return {w for w in re.split(r"[^\w]+", s) if w}


def overlap(a: str | None, b: str | None) -> float:
    """Shared words over the shorter name's words (0..1)."""
    x, y = _words(a), _words(b)
    small, big = (x, y) if len(x) <= len(y) else (y, x)
    return len(small & big) / len(small) if small else 0.0


def _year(v) -> str | None:
    m = re.match(r"\s*(\d{4})", str(v or ""))
    return m.group(1) if m else None


VARIOUS = re.compile(r"^\s*(various( artists)?|va)\s*$", re.I)


def score(local: list[dict], cand: dict, evidence: str | None = None, artist: str | None = None) -> dict:
    """
    {score 0..1, matched, reasons, ok}. `ok`: the titles agree, the artist
    isn't someone else's and the lengths aren't far off, so it can be proposed
    (always true with evidence: a barcode or ISRC says which album). Another
    artist's album of the same songs (a tribute, a cover album) fails here.
    """
    tracks = cand.get("tracks") or []
    n, m = len(local), len(tracks)
    q = albummatch.quality(local, tracks)
    matched = q["matched"]
    coverage = matched / n if n else 0.0
    count_fit = 1.0 if m == n else max(0.0, 1 - abs(m - n) / max(n, 1))
    by_key = {t["key"]: t for t in tracks}
    diffs = [abs(l["duration"] - by_key[k]["duration"]) for l in local
             for k, _ in [q["pairs"][l["id"]]] if k and l.get("duration") and by_key[k].get("duration")]
    mean_diff = sum(diffs) / len(diffs) if diffs else None
    len_fit = 0.5 if mean_diff is None else 1.0 if mean_diff <= 2 else 0.5 if mean_diff <= 10 else 0.0
    far = mean_diff is not None and mean_diff > 30
    years = {_year(l.get("year")) for l in local} - {None}
    year_hit = len(years) == 1 and _year(cand.get("date")) in years
    other_artist = bool(artist and not VARIOUS.match(artist) and cand.get("artist")
                        and not VARIOUS.match(cand["artist"]) and overlap(artist, cand["artist"]) == 0)
    s = 0.55 * coverage + 0.2 * q["quality"] + 0.15 * count_fit + 0.1 * len_fit + (0.03 if year_hit else 0)
    if far:
        s -= 0.25
    if other_artist:
        s *= 0.6
    reasons = [f"{matched}/{n} titles"]
    if other_artist:
        reasons.append(f"by {cand['artist']}")
    if m != n:
        reasons.append(f"{m} tracks there")
    if mean_diff is not None:
        reasons.append(f"length ±{round(mean_diff)} s")
    if year_hit:
        reasons.append(f"year {next(iter(years))}")
    if evidence:
        reasons.append(evidence)
    ok = bool(evidence) or (coverage >= 0.5 and matched >= min(2, n) and not other_artist and not far)
    return {"score": round(max(0.0, min(s, 1.0)), 3), "matched": matched, "reasons": reasons, "ok": ok}


def candidate(cand: dict, sc: dict) -> dict:
    """What is kept and shown about a scored album (no track list)."""
    keep = ("source", "id", "title", "artist", "date", "label", "barcode", "link", "cover", "cover_size")
    return {**{k: cand.get(k) for k in keep}, "tracks": len(cand.get("tracks") or []),
            "score": sc["score"], "reasons": " · ".join(sc["reasons"])}


def find(local: list[dict], prov, *, album: str | None, artist: str | None,
         barcode: str | None = None, isrcs: list[str] | None = None) -> dict:
    """
    {method, link, candidates}: `link` is the album the evidence names
    (method "barcode" / "isrc"), else None and `candidates` holds the search
    hits that passed the title gate, best first (method "search", or None
    when there were none).
    """
    # A barcode names an edition, not our tracks: a disc tagged with a bigger
    # edition's barcode (a Legacy Edition's) must not link to it outright. It
    # links only when at least half our titles are on it; else it is offered
    # like a search hit, for the user to confirm.
    weak = None
    if barcode:
        best, best_matched = None, 0
        for aid in prov.by_barcode(barcode)[:3]:
            a = prov.album(aid)
            if a:
                sc = score(local, a, "barcode", artist)
                if not best or sc["score"] > best["score"]:
                    best, best_matched = candidate(a, sc), sc["matched"]
        if best and best_matched * 2 >= len(local):
            return {"method": "barcode", "link": best, "candidates": [best]}
        weak = best
    by_isrc = getattr(prov, "album_by_isrc", None)
    sample = [i for i in dict.fromkeys(x.strip().upper() for x in isrcs or [] if x and x.strip())][:ISRC_SAMPLE]
    if by_isrc and sample:
        hits = Counter(a for a in (by_isrc(i) for i in sample) if a)
        if hits:
            aid, k = hits.most_common(1)[0]
            if k * 2 > len(sample):
                a = prov.album(aid)
                if a:
                    best = candidate(a, score(local, a, f"ISRC {k}/{len(sample)}", artist))
                    return {"method": "isrc", "link": best, "candidates": [best]}
    hits = prov.search(album, artist) if (album or artist) else []
    n = len(local)
    # Read in full only hits whose name is ours (or, with no album name, whose
    # artist is), the same track count first.
    named = [h for h in hits if (overlap(album, h.get("title")) >= NAME_MIN if album
                                 else overlap(artist, h.get("artist")) >= NAME_MIN)]
    named.sort(key=lambda h: (not artist or overlap(artist, h.get("artist")) == 0, h.get("tracks") != n,
                              -overlap(album or artist, h.get("title") if album else h.get("artist"))))
    out = []
    for h in named[:SEARCH_PROBES]:
        a = prov.album(h["id"])
        if not a:
            continue
        sc = score(local, a, artist=artist)
        if sc["ok"]:
            out.append(candidate(a, sc))
    out.sort(key=lambda c: -c["score"])
    if weak and not any(c["source"] == weak["source"] and c["id"] == weak["id"] for c in out):
        out.insert(0, weak)
    return {"method": "search" if out else None, "link": None, "candidates": out}
