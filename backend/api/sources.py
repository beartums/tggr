"""
Album sources (core/sources.py): find an album folder on Deezer / iTunes,
confirm / relink / unlink it, the covers the links have, and the label / year
fill. Lookups are slow (iTunes allows ~20 calls a minute), so they run in a
worker thread and hold no database connection while waiting.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.config import _load as load_settings
from core import sourcelinks, sources
from core.database import db
from core.providers.http import SourceError

router = APIRouter()


class Selection(BaseModel):
    track_ids: list[int]
    refresh: bool = False
    sources: list[str] | None = None


class LinkReq(BaseModel):
    directory: str
    source: str
    album_id: str | None = None        # with: link to this album (picked); without: confirm the link there is


class FillReq(BaseModel):
    track_ids: list[int]
    preview: bool = True


def _folder(track_ids: list[int]) -> str:
    if not track_ids:
        raise HTTPException(400, "No tracks")
    with db() as conn:
        try:
            return sources.folder_of(conn, track_ids)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None


def _source(name: str) -> str:
    if name not in sources.PROVIDERS:
        raise HTTPException(404, "Unknown source")
    return name


@router.get("/links")
def get_links(directory: str):
    """Each source's link for one album folder: {source: link}."""
    with db() as conn:
        return sourcelinks.get(conn, directory)


@router.post("/match")
async def match(req: Selection):
    """Find the selection's album folder on each source (kept links reused unless refresh)."""
    d = _folder(req.track_ids)
    cfg = load_settings()
    srcs = [_source(s) for s in req.sources] if req.sources else None
    return await asyncio.to_thread(sources.match_album, d, cfg, srcs, req.refresh)


@router.post("/link")
async def link(req: LinkReq):
    _source(req.source)
    if not req.album_id:
        with db() as conn:
            if not sourcelinks.confirm(conn, req.directory, req.source):
                raise HTTPException(404, "No link to confirm")
            return sourcelinks.get(conn, req.directory)[req.source]
    try:
        return await asyncio.to_thread(sources.link_manual, req.directory, req.source, req.album_id.strip())
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except (ValueError, SourceError) as e:
        raise HTTPException(400, str(e)) from None


@router.post("/unlink")
def unlink(req: LinkReq):
    _source(req.source)
    with db() as conn:
        return {"removed": sourcelinks.unlink(conn, req.directory, req.source)}


@router.post("/covers")
async def covers(req: Selection):
    """The cover each source has for the selection's album folder, in the cover order."""
    d = _folder(req.track_ids)
    cfg = load_settings()
    return {"directory": d, **await asyncio.to_thread(sources.covers, d, cfg, cfg.discogs_token)}


@router.post("/fill")
def fill(req: FillReq):
    """Label / year from the confirmed links, where the tag is empty: the plan (preview) or staged."""
    if not req.track_ids:
        raise HTTPException(400, "No tracks")
    with db() as conn:
        return sources.fill(conn, req.track_ids, load_settings(), req.preview)
