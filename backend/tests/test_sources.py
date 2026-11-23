"""Album sources: the matching ladder, the links, the covers / fill, and the providers' parsing."""
from __future__ import annotations

import pytest

from core import sourcelinks, sourcematch
from core.database import db


def _local(titles, durs, year="1959"):
    return [{"id": i + 1, "title": t, "filename": f"{i + 1:02d} {t}.flac", "track_number": str(i + 1),
             "disc_number": "1", "duration": d, "year": year} for i, (t, d) in enumerate(zip(titles, durs))]


KOB = _local(["So What", "Freddie Freeloader", "Blue in Green", "All Blues", "Flamenco Sketches"],
             [562, 586, 337, 693, 566])


def _album(aid, titles, durs, artist="Miles Davis", date="1959-08-17", source="fake", label=None):
    return {"source": source, "id": aid, "title": "Kind of Blue", "artist": artist, "date": date, "label": label,
            "barcode": None, "link": f"https://x/{aid}", "cover": f"https://img/{aid}.jpg", "cover_size": 1000,
            "tracks": [{"key": f"1-{i + 1}", "disc": 1, "position": i + 1, "title": t, "duration": d}
                       for i, (t, d) in enumerate(zip(titles, durs))]}


SAME = _album("1", [t["title"] for t in KOB], [d["duration"] + 2 for d in KOB])
COVER_ALBUM = _album("2", [t["title"] for t in KOB], [200, 210, 190, 230, 205], artist="Eric Skye")


class FakeProv:
    NAME = "fake"

    def __init__(self, albums, barcode=None, isrc=None, hits=None):
        self.albums, self._barcode, self._isrc, self._hits, self.calls = albums, barcode or {}, isrc, hits, []

    def by_barcode(self, code):
        self.calls.append(("barcode", code))
        return self._barcode.get(code, [])

    def search(self, album, artist):
        self.calls.append(("search", album, artist))
        return self._hits if self._hits is not None else [
            {"id": a["id"], "title": a["title"], "artist": a["artist"], "tracks": len(a["tracks"])}
            for a in self.albums.values()]

    def album(self, aid):
        self.calls.append(("album", aid))
        return self.albums.get(aid)


class FakeIsrcProv(FakeProv):
    def album_by_isrc(self, isrc):
        return self._isrc.get(isrc)


# ─── The ladder ──────────────────────────────────────────────────────────────

def test_score_reasons_and_gate():
    sc = sourcematch.score(KOB, SAME, artist="Miles Davis")
    assert sc["ok"] and sc["score"] > 0.95
    assert sc["reasons"] == ["5/5 titles", "length ±2 s", "year 1959"]


def test_another_artists_album_of_the_same_songs_is_not_proposed():
    sc = sourcematch.score(KOB, COVER_ALBUM, artist="Miles Davis")
    assert not sc["ok"]
    assert "by Eric Skye" in sc["reasons"]


def test_names_alone_never_propose():
    other = _album("3", ["Come Together", "Something", "Because", "Sun King", "The End"], [260, 183, 165, 146, 141])
    r = sourcematch.find(KOB, FakeProv({"3": other}), album="Kind of Blue", artist="Miles Davis")
    assert r == {"method": None, "link": None, "candidates": []}


def test_barcode_links_outright_and_skips_search():
    prov = FakeProv({"1": SAME}, barcode={"888880696069": ["1"]})
    r = sourcematch.find(KOB, prov, album="Kind of Blue", artist="Miles Davis", barcode="888880696069")
    assert r["method"] == "barcode" and r["link"]["id"] == "1"
    assert "barcode" in r["link"]["reasons"]
    assert not any(c[0] == "search" for c in prov.calls)


def test_barcode_of_a_bigger_edition_is_offered_not_linked():
    # Our 5 tracks; the barcode's edition has only 2 of them (a Legacy-style set).
    other = {**SAME, "tracks": SAME["tracks"][:2] + [dict(t, key=f"9-{i}", title=f"Bonus {i}")
                                                     for i, t in enumerate(SAME["tracks"][2:])]}
    prov = FakeProv({"1": other}, barcode={"888880696069": ["1"]}, hits=[])
    r = sourcematch.find(KOB, prov, album="Kind of Blue", artist="Miles Davis", barcode="888880696069")
    assert r["link"] is None and r["method"] == "search"
    assert r["candidates"][0]["id"] == "1" and "barcode" in r["candidates"][0]["reasons"]


def test_isrc_needs_a_majority():
    prov = FakeIsrcProv({"1": SAME}, isrc={"A": "1", "B": "1", "C": "9"})
    r = sourcematch.find(KOB, prov, album="Kind of Blue", artist="Miles Davis", isrcs=["a", "B", "C"])
    assert r["method"] == "isrc" and "ISRC 2/3" in r["link"]["reasons"]
    split = FakeIsrcProv({"1": SAME}, isrc={"A": "1", "B": "9"}, hits=[])
    r = sourcematch.find(KOB, split, album="Kind of Blue", artist="Miles Davis", isrcs=["A", "B"])
    assert r["method"] is None, "one of two is not a majority: falls through to search (empty here)"


def test_search_ranks_and_keeps_only_passing_hits():
    prov = FakeProv({"1": SAME, "2": COVER_ALBUM})
    r = sourcematch.find(KOB, prov, album="Kind of Blue", artist="Miles Davis")
    assert r["method"] == "search"
    assert [c["id"] for c in r["candidates"]] == ["1"]
    assert r["candidates"][0]["reasons"].startswith("5/5 titles")


def test_barcode_spellings():
    from core.providers.deezer import barcodes
    assert barcodes("0888880696069") == ["0888880696069", "888880696069"]
    assert barcodes("888880696069") == ["888880696069", "0888880696069"]
    assert barcodes("") == []


# ─── Providers: parsing what the services send (checked live 2026-11-22) ─────

def test_deezer_album_parsing(monkeypatch):
    from core.providers import deezer
    pages = {
        "/album/42": {"id": 42, "title": "Kind Of Blue", "upc": "888880696069", "label": "Columbia",
                      "release_date": "2009-01-19", "cover_xl": "https://c/1000.jpg", "link": "https://d/42",
                      "artist": {"name": "Miles Davis"}},
        "/album/42/tracks?limit=100": {"data": [
            {"title": "So What (feat. John Coltrane)", "title_short": "So What", "duration": 562,
             "track_position": 1, "disk_number": 1, "isrc": "USSM15900113"}], "next": None},
        "/album/upc:888880696069": {"id": 42},
        "/album/upc:0888880696069": {"error": {"code": 800, "message": "no data"}},
    }
    monkeypatch.setattr(deezer, "_get", lambda path: (pages.get(path) or {}).get("error") and None or pages.get(path))
    a = deezer.album("42")
    assert (a["label"], a["date"], a["cover_size"]) == ("Columbia", "2009-01-19", 1000)
    assert a["tracks"][0] == {"key": "1-1", "disc": 1, "position": 1, "title": "So What", "duration": 562.0,
                              "isrc": "USSM15900113"}
    assert deezer.by_barcode("888880696069") == ["42"]


def test_deezer_no_data_is_none_and_errors_raise(monkeypatch):
    from core.providers import deezer, http
    monkeypatch.setattr(deezer, "get_json", lambda url, t: {"error": {"code": 800, "message": "no data"}})
    assert deezer.album("1") is None
    monkeypatch.setattr(deezer, "get_json", lambda url, t: {"error": {"code": 4, "message": "Quota limit exceeded"}})
    with pytest.raises(http.SourceError):
        deezer.album("1")


def test_itunes_album_parsing(monkeypatch):
    from core.providers import itunes
    rows = [{"wrapperType": "collection", "collectionId": 268443092, "collectionName": "Kind of Blue",
             "artistName": "Miles Davis", "releaseDate": "1959-08-17T07:00:00Z",
             "artworkUrl100": "https://is1/x.jpg/100x100bb.jpg", "collectionViewUrl": "https://music/kob"},
            {"wrapperType": "track", "trackName": "So What", "trackNumber": 1, "discNumber": 1,
             "trackTimeMillis": 547983}]
    monkeypatch.setattr(itunes, "_get", lambda path, **p: rows)
    a = itunes.album("268443092")
    assert a["cover"] == "https://is1/x.jpg/1200x1200bb.jpg"
    assert (a["date"], a["label"]) == ("1959-08-17", None)
    assert a["tracks"][0]["duration"] == pytest.approx(547.983)


# ─── Links, covers, fill (database) ──────────────────────────────────────────

def _tracks(conn, directory="/m/Miles Davis/Kind of Blue", label=None, year=""):
    ids = []
    for t in KOB:
        cur = conn.execute(
            "INSERT INTO tracks (path, filename, directory, format, size, mtime, title, artist, album, album_artist,"
            " year, track_number, disc_number, duration, label, scanned_at) VALUES (?, ?, ?, 'flac', 1, 0, ?,"
            " 'Miles Davis', 'Kind of Blue', 'Miles Davis', ?, ?, '1', ?, ?, 0)",
            (f"{directory}/{t['filename']}", t["filename"], directory, t["title"], year, t["track_number"],
             t["duration"], label))
        ids.append(cur.lastrowid)
    return ids


def _cfg(**kw):
    from api.config import AppSettings
    return AppSettings(**kw)


def test_links_store_and_quality_sql(temp_db):
    d = "/m/Miles Davis/Kind of Blue"
    with db() as conn:
        _tracks(conn, d)
        c = sourcematch.candidate({**SAME, "source": "deezer"}, sourcematch.score(KOB, SAME))
        link = sourcelinks.save_link(conn, d, c, "search", False)
        assert (link["status"], link["confirmed"], link["title"]) == ("linked", False, "Kind of Blue")
        n = conn.execute(f"SELECT COUNT(*) FROM tracks WHERE {sourcelinks.UNCONFIRMED_SQL}").fetchone()[0]
        assert n == 5
        assert sourcelinks.confirm(conn, d, "deezer")
        assert conn.execute(f"SELECT COUNT(*) FROM tracks WHERE {sourcelinks.UNCONFIRMED_SQL}").fetchone()[0] == 0
        no_cover = "COALESCE(has_cover, 0) = 0"
        linked = conn.execute(f"SELECT COUNT(*) FROM tracks WHERE {sourcelinks.no_source_sql(no_cover)}").fetchone()[0]
        assert linked == 0, "a linked source: not 'no cover source'"
        sourcelinks.save_missing(conn, d, "deezer")
        q = lambda **kw: conn.execute(  # noqa: E731
            f"SELECT COUNT(*) FROM tracks WHERE {sourcelinks.no_source_sql(no_cover, **kw)}").fetchone()[0]
        assert q() == 5 and q(not_found=False) == 0
        assert q(before=0.0) == 0, "tried after the cutoff: left out"
        assert q(before=4e9) == 5
        assert sourcelinks.unlink(conn, d, "deezer") and sourcelinks.get(conn, d) == {}


def test_match_album_records_links_and_missing(temp_db, monkeypatch):
    from core import sources
    d = "/m/Miles Davis/Kind of Blue"
    with db() as conn:
        _tracks(conn, d)
    found = FakeProv({"1": {**SAME, "source": "deezer"}})
    nothing = FakeProv({}, hits=[])
    monkeypatch.setattr(sources, "PROVIDERS", {"deezer": found, "itunes": nothing})
    r = sources.match_album(d, _cfg())
    assert r["links"]["deezer"]["method"] == "search" and not r["links"]["deezer"]["confirmed"]
    assert r["links"]["itunes"]["status"] == "missing"
    calls = len(found.calls)
    sources.match_album(d, _cfg())
    assert len(found.calls) == calls, "kept links are reused, not looked up again"
    r = sources.match_album(d, _cfg(source_search_policy="auto", source_auto_threshold=0.9), refresh=True)
    assert r["links"]["deezer"]["confirmed"], "auto policy: accepted at the threshold"


def test_fill_only_empty_tags_from_confirmed_links(temp_db, monkeypatch):
    from core import sources
    d = "/m/Miles Davis/Kind of Blue"
    with db() as conn:
        ids = _tracks(conn, d, label=None, year="")
        conn.execute("UPDATE tracks SET year = '1959' WHERE id = ?", (ids[0],))
        conn.execute("UPDATE tracks SET label = 'Columbia' WHERE id = ?", (ids[1],))
        cand = sourcematch.candidate({**SAME, "source": "deezer", "label": "Columbia/Legacy", "date": "2009-01-19"},
                                     sourcematch.score(KOB, SAME))
        sourcelinks.save_link(conn, d, cand, "search", False)
    monkeypatch.setattr(sources, "_file_label", lambda path: None)
    with db() as conn:
        plan = sources.fill(conn, ids, _cfg())
    assert plan["changes"] == 0, "an unconfirmed link fills nothing"
    with db() as conn:
        sourcelinks.confirm(conn, d, "deezer")
        plan = sources.fill(conn, ids, _cfg())
        assert plan["albums"][0]["values"] == {"label": {"value": "Columbia/Legacy", "source": "Deezer"},
                                               "year": {"value": "2009", "source": "Deezer"}}
        assert plan["changes"] == 5 and plan["albums"][0]["fields"] == ["label", "year"]
        no_label = sources.fill(conn, ids, _cfg(source_fields={"deezer": ["cover", "year"]}))
        assert no_label["albums"][0]["fields"] == ["year"] and no_label["changes"] == 4
        done = sources.fill(conn, ids, _cfg(), preview=False)
        assert done["staged"] is True
        staged = {r["track_id"]: r["tags"] for r in conn.execute("SELECT track_id, tags FROM staged")}
    import json
    tags = {k: json.loads(v) for k, v in staged.items()}
    assert "year" not in tags[ids[0]] and tags[ids[0]]["label"] == "Columbia/Legacy"
    assert "label" not in tags[ids[1]] and tags[ids[1]]["year"] == "2009"


def test_covers_follow_the_order(temp_db, monkeypatch):
    from core import sources
    d = "/m/Miles Davis/Kind of Blue"
    with db() as conn:
        _tracks(conn, d)
    monkeypatch.setattr(sources, "PROVIDERS", {"deezer": FakeProv({"1": {**SAME, "source": "deezer"}}),
                                               "itunes": FakeProv({"7": {**SAME, "id": "7", "source": "itunes"}})})
    r = sources.covers(d, _cfg(cover_order=["deezer", "caa", "itunes", "discogs"]))
    assert r["order"] == ["deezer", "caa", "itunes", "discogs"]
    assert [c["source"] for c in r["covers"]] == ["deezer", "itunes"]
    r = sources.covers(d, _cfg(source_fields={"deezer": ["label"], "itunes": ["cover"]}))
    assert [c["source"] for c in r["covers"]] == ["itunes"], "deezer may not give covers"


# ─── Settings and API ────────────────────────────────────────────────────────

def test_settings_validation_and_scan_tag_migration(client, tmp_path):
    r = client.patch("/api/config", json={"cover_order": ["deezer", "bogus", "deezer"],
                                          "source_fields": {"itunes": ["cover", "label"], "nope": ["cover"]}})
    assert r.status_code == 200
    s = r.json()
    assert s["cover_order"] == ["deezer", "caa", "itunes", "discogs"]
    assert s["source_fields"] == {"itunes": ["cover"]}, "iTunes has no label; unknown sources dropped"
    assert client.patch("/api/config", json={"source_search_policy": "maybe"}).status_code == 400
    assert client.patch("/api/config", json={"source_auto_threshold": 0.2}).status_code == 400
    assert client.patch("/api/config", json={"sources_view_not_found_before": "soon"}).status_code == 400
    # Settings saved before label existed: label is scanned now, not left out.
    import json
    from core.config import settings
    f = settings.settings_file
    data = json.loads(f.read_text())
    data["scan_tags"] = ["title", "artist"]
    data.pop("scan_tags_known", None)
    f.write_text(json.dumps(data))
    assert client.get("/api/config").json()["scan_tags"] == ["title", "artist", "label"]


def test_source_test_and_quality_keys(client, monkeypatch):
    from core.providers import deezer
    monkeypatch.setattr(deezer, "test", lambda: {"ok": True, "message": "Deezer answered (no key needed)"})
    assert client.post("/api/config/test/deezer", json={}).json()["ok"]
    issues = client.get("/api/library/issues").json()
    assert {"src_unconfirmed", "covers_no_source"} <= set(issues)


def test_api_link_confirm_unlink(client):
    d = "/m/x"
    with db() as conn:
        _tracks(conn, d)
        c = sourcematch.candidate({**SAME, "source": "itunes"}, sourcematch.score(KOB, SAME))
        sourcelinks.save_link(conn, d, c, "search", False)
    assert client.get("/api/sources/links", params={"directory": d}).json()["itunes"]["confirmed"] is False
    assert client.post("/api/sources/link", json={"directory": d, "source": "itunes"}).json()["confirmed"] is True
    assert client.post("/api/sources/unlink", json={"directory": d, "source": "itunes"}).json() == {"removed": True}
    assert client.post("/api/sources/link", json={"directory": d, "source": "nope"}).status_code == 404
