"""Chunked video upload, so a 1+ GB file can reach the Pod through a web proxy that cuts any single request after
about 100 seconds / 100 MB (Runpod's HTTPS address).

The browser cuts the file into pieces (16 MB) and sends each as its own short request:
  POST /upload/chunk?upload_id=..&index=..&total=..&name=..   raw bytes of one piece
  GET  /upload/status?upload_id=..                            which pieces are already here (a retry skips them)
  POST /upload/finish?upload_id=..&total=..&name=..           stitch the pieces into inbox/<name>
Pieces live in data/uploads/<upload_id>/ until they are stitched (or 2 days old)."""
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, Request

from . import config, ingest
from .main import A, app

_ID = re.compile(r"^[A-Za-z0-9_.-]{6,100}$")
MAX_PIECES = 4000
STALE_S = 2 * 24 * 3600


def _dir(upload_id: str) -> Path:
    if not _ID.match(upload_id) or ".." in upload_id:
        raise HTTPException(400, "bad upload id")
    return config.DATA / "uploads" / upload_id


def _name(name: str) -> str:
    n = Path(name or "upload.mp4").name
    if Path(n).suffix.lower() not in ingest.VIDEO_EXT:
        raise HTTPException(400, "unsupported file type")
    return n


def _sweep() -> None:
    """Forget half-finished uploads nobody came back for."""
    root = config.DATA / "uploads"
    if not root.is_dir():
        return
    for d in root.iterdir():
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > STALE_S:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


@app.post("/upload/chunk", dependencies=A)
async def upload_chunk(request: Request, upload_id: str, index: int, total: int, name: str):
    _name(name)
    if not (0 <= index < total <= MAX_PIECES):
        raise HTTPException(400, "bad piece number")
    d = _dir(upload_id)
    if not d.exists():
        _sweep()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{index}.{uuid.uuid4().hex[:6]}.part"
    size = 0
    try:
        with tmp.open("wb") as f:
            async for part in request.stream():
                f.write(part)
                size += len(part)
        if size == 0:
            raise HTTPException(400, "empty piece")
        tmp.replace(d / f"{index:05d}")  # a piece appears whole or not at all
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "index": index, "bytes": size}


@app.get("/upload/status", dependencies=A)
def upload_status(upload_id: str):
    d = _dir(upload_id)
    have = sorted(int(p.name) for p in d.iterdir() if re.fullmatch(r"\d{5}", p.name)) if d.is_dir() else []
    return {"have": have}


@app.post("/upload/finish", dependencies=A)
def upload_finish(upload_id: str, total: int, name: str):
    n = _name(name)
    d = _dir(upload_id)
    missing = [i for i in range(total) if not (d / f"{i:05d}").is_file()] if d.is_dir() else list(range(total))
    if missing:
        raise HTTPException(409, f"{len(missing)} piece(s) are missing: {missing[:5]}")
    dst = config.INBOX / n
    tmp = config.INBOX / f".{n}.{uuid.uuid4().hex[:8]}.part"  # same rule as /upload: whole file or nothing, new inode
    try:
        with tmp.open("wb") as out:
            for i in range(total):
                with (d / f"{i:05d}").open("rb") as src:
                    shutil.copyfileobj(src, out, 8 << 20)
        if tmp.stat().st_size == 0:
            raise HTTPException(400, "the uploaded file is empty")
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    shutil.rmtree(d, ignore_errors=True)
    return {"filename": n, "bytes": dst.stat().st_size}
