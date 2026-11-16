import json
import logging
import os
import shutil
import tempfile
from threading import Lock
from urllib.parse import urlparse

from mutagen.mp4 import MP4

import config
import downloader

logger = logging.getLogger(__name__)

PENDING_FILE = os.path.join(config.DATA_DIR, "pending.json")
_lock = Lock()

_ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com", "youtu.be"}


def _load() -> list[dict]:
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    tmp = PENDING_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)
    os.replace(tmp, PENDING_FILE)


def add(dir_name: str, filename: str) -> None:
    with _lock:
        items = _load()
        if not any(i["dir"] == dir_name and i["file"] == filename for i in items):
            items.append({"dir": dir_name, "file": filename})
            _save(items)


def _path(dir_name: str, filename: str) -> str:
    return os.path.join(config.MUSIC_LIBRARY_DIR, dir_name, filename)


def _first(tags, key, default=""):
    v = tags.get(key) if tags else None
    return v[0] if v else default


def list_grouped() -> list[dict]:
    """Pending songs grouped by album folder, with tags read from the files."""
    with _lock:
        items = _load()

    albums: dict[str, dict] = {}
    kept = []
    for item in items:
        path = _path(item["dir"], item["file"])
        if not os.path.exists(path):
            continue
        kept.append(item)
        try:
            tags = MP4(path).tags
        except Exception:
            tags = None
        album = albums.setdefault(item["dir"], {
            "dir": item["dir"],
            "album": _first(tags, "\u00a9alb", item["dir"]),
            "artist": _first(tags, "aART") or _first(tags, "\u00a9ART"),
            "date": _first(tags, "\u00a9day"),
            "hasCover": False,
            "songs": [],
        })
        album["hasCover"] = album["hasCover"] or bool(tags and tags.get("covr"))
        trkn = _first(tags, "trkn", (0, 0))
        album["songs"].append({
            "file": item["file"],
            "title": _first(tags, "\u00a9nam", item["file"]),
            "artists": list(tags.get("\u00a9ART", [])) if tags else [],
            "track": trkn[0],
        })

    if len(kept) != len(items):
        with _lock:
            _save(kept)

    result = list(albums.values())
    for a in result:
        a["songs"].sort(key=lambda s: s["track"])
    result.sort(key=lambda a: a["album"].lower())
    return result


def cover(dir_name: str) -> tuple[bytes, str] | None:
    """Cover of the first pending song in the folder (all songs share one cover)."""
    with _lock:
        items = [i for i in _load() if i["dir"] == dir_name]
    for item in items:
        try:
            covr = MP4(_path(item["dir"], item["file"])).tags.get("covr")
        except Exception:
            continue
        if covr:
            mime = "image/png" if covr[0].imageformat == covr[0].FORMAT_PNG else "image/jpeg"
            return bytes(covr[0]), mime
    return None


def dismiss(dir_name: str | None = None, filename: str | None = None) -> int:
    """Remove entries from pending without touching audio. No args = everything."""
    with _lock:
        items = _load()
        kept = [
            i for i in items
            if not ((dir_name is None or i["dir"] == dir_name)
                    and (filename is None or i["file"] == filename))
        ]
        _save(kept)
        return len(items) - len(kept)


def delete(dir_name: str, filename: str | None = None) -> None:
    """Delete one song, or the whole album folder, from disk and from pending."""
    root = os.path.realpath(config.MUSIC_LIBRARY_DIR)
    target = os.path.realpath(_path(dir_name, filename) if filename else _path(dir_name, ""))
    album_dir = os.path.realpath(os.path.join(root, dir_name))
    # Must be exactly one level below the library root (album) or inside it (song).
    if os.path.dirname(album_dir) != root or (filename and os.path.dirname(target) != album_dir):
        raise ValueError("Invalid path")

    if filename:
        if os.path.isfile(target):
            os.remove(target)
    elif os.path.isdir(target):
        shutil.rmtree(target)
    dismiss(dir_name, filename)


def replace_audio(dir_name: str, filename: str, url: str) -> None:
    """Swap the song's audio for the given YouTube URL, keeping its tags and cover."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "") not in _ALLOWED_HOSTS:
        raise ValueError("Not a YouTube URL")

    with _lock:
        if not any(i["dir"] == dir_name and i["file"] == filename for i in _load()):
            raise LookupError("Song is not pending")

    target = _path(dir_name, filename)
    old = MP4(target)

    tmp_dir = tempfile.mkdtemp(prefix="replace-")
    try:
        tmp_path = os.path.join(tmp_dir, "new.m4a")
        if not downloader._download_track(url, tmp_path):
            raise RuntimeError("Download failed")

        new = MP4(tmp_path)
        new.delete()
        for key, value in (old.tags or {}).items():
            new[key] = value
        new.save()

        shutil.move(tmp_path, target)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    dismiss(dir_name, filename)
