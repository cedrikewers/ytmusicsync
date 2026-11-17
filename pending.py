import hashlib
import json
from array import array
import logging
import math
import os
import shutil
import subprocess
import tempfile
import uuid
from threading import Lock, RLock
from urllib.parse import urlparse

import requests
from mutagen.mp4 import MP4, MP4Cover

import config
import downloader
import library

logger = logging.getLogger(__name__)

PENDING_FILE = os.path.join(config.DATA_DIR, "pending.json")
MISSING_COVER_DIR = os.path.join(config.DATA_DIR, "missing_covers")
STAGING_DIR = config.STAGING_DIR

_lock = Lock()  # guards pending.json
_op_lock = RLock()  # serialises file-moving operations

ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com", "youtu.be"}

# Entries: {"dir", "file"} plus optional "missing" + "meta" for failed downloads.
# Staged (non-music) songs have dir == "" and live in STAGING_DIR until filed into an album.


def _is_allowed(url: str, hosts: set[str]) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and (p.hostname or "") in hosts


def is_plain_youtube_url(url: str) -> bool:
    return _is_allowed(url, ALLOWED_HOSTS - {"music.youtube.com"})


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


def _abs(dir_name: str, filename: str) -> str:
    base = os.path.join(config.MUSIC_LIBRARY_DIR, dir_name) if dir_name else STAGING_DIR
    return os.path.join(base, filename)


def _missing_cover_path(dir_name: str, filename: str) -> str:
    return os.path.join(MISSING_COVER_DIR, hashlib.sha1(f"{dir_name}/{filename}".encode()).hexdigest())


def _mtime(path: str) -> int:
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


def _mime(data: bytes) -> str:
    return "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"


def _first(tags, key, default=""):
    v = tags.get(key) if tags else None
    return v[0] if v else default


def _find(dir_name: str, filename: str) -> dict:
    with _lock:
        for i in _load():
            if i["dir"] == dir_name and i["file"] == filename:
                return i
    raise LookupError("Song is not pending")


def _update_entry(old_dir: str, old_file: str, **changes) -> None:
    with _lock:
        items = _load()
        for i in items:
            if i["dir"] == old_dir and i["file"] == old_file:
                i.update(changes)
                break
        _save(items)


def _remove(pred) -> int:
    with _lock:
        items = _load()
        removed = [i for i in items if pred(i)]
        _save([i for i in items if not pred(i)])
    for i in removed:
        if i.get("missing"):
            try:
                os.remove(_missing_cover_path(i["dir"], i["file"]))
            except OSError:
                pass
    return len(removed)


def add(dir_name: str, filename: str) -> None:
    with _lock:
        items = _load()
        if not any(i["dir"] == dir_name and i["file"] == filename for i in items):
            items.append({"dir": dir_name, "file": filename})
            _save(items)


def add_missing(dir_name: str, filename: str, meta: dict, cover_data: bytes | None) -> None:
    """Record a failed download with the metadata resolved for it."""
    if cover_data:
        os.makedirs(MISSING_COVER_DIR, exist_ok=True)
        with open(_missing_cover_path(dir_name, filename), "wb") as f:
            f.write(cover_data)
    with _lock:
        items = [i for i in _load() if not (i["dir"] == dir_name and i["file"] == filename)]
        items.append({"dir": dir_name, "file": filename, "missing": True, "meta": meta})
        _save(items)


def _fetch_thumbnail(video_id: str | None) -> bytes | None:
    if not video_id:
        return None
    for name in ("maxresdefault", "hqdefault"):
        try:
            resp = requests.get(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", timeout=15)
            if resp.ok and resp.content:
                return resp.content
        except requests.RequestException:
            continue
    return None


def stage_video(url: str, progress_cb=None) -> str:
    """Download a plain YouTube video's audio into staging. Returns its title."""
    if progress_cb:
        progress_cb("Fetching video info...")
    info = downloader.fetch_video_info(url)
    if not info:
        raise RuntimeError("Could not read video info")

    title = info.get("title") or "Unknown"
    artist = info.get("artist") or info.get("uploader") or info.get("channel") or ""
    raw = info.get("upload_date") or ""
    date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(raw) == 8 else ""

    os.makedirs(STAGING_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.m4a"
    path = os.path.join(STAGING_DIR, filename)

    if progress_cb:
        progress_cb("Downloading...")
    if not downloader._download_track(url, path):
        raise RuntimeError("Download failed")

    downloader._tag_m4a(
        filepath=path, title=title, artists=[artist] if artist else [], album="", album_artist="",
        track_number=1, track_total=1, disc_number=1, disc_total=1, date=date, lyrics=None,
        cover_data=_fetch_thumbnail(info.get("id")),
    )
    add("", filename)
    return title


def list_grouped() -> list[dict]:
    """Pending songs grouped by album folder; staged songs form a group with dir == ""."""
    with _lock:
        items = _load()

    albums: dict[str, dict] = {}
    stale: set[tuple[str, str]] = set()
    for item in items:
        key = item["dir"]
        missing = bool(item.get("missing"))
        if missing:
            m = item["meta"]
            cp = _missing_cover_path(key, item["file"])
            info = {
                "title": m["title"], "artists": m["artists"], "track": m["track_number"],
                "album": m["album"], "album_artist": m["album_artist"], "date": m["date"],
                "has_cover": os.path.exists(cp), "rev": _mtime(cp),
            }
        else:
            path = _abs(key, item["file"])
            if not os.path.exists(path):
                stale.add((key, item["file"]))
                continue
            try:
                tags = MP4(path).tags
            except Exception:
                tags = None
            info = {
                "title": _first(tags, "\u00a9nam", item["file"]),
                "artists": list(tags.get("\u00a9ART", [])) if tags else [],
                "track": _first(tags, "trkn", (0, 0))[0],
                "album": _first(tags, "\u00a9alb"),
                "album_artist": _first(tags, "aART"),
                "date": _first(tags, "\u00a9day"),
                "has_cover": bool(tags and tags.get("covr")),
                "rev": _mtime(path),
            }

        album = albums.setdefault(key, {
            "dir": key,
            "album": (info["album"] or key) if key else "",
            "artist": info["album_artist"] if key else "",
            "date": info["date"],
            "hasCover": False,
            "rev": 0,
            "staged": not key,
            "songs": [],
        })
        album["hasCover"] = album["hasCover"] or info["has_cover"]
        album["rev"] = max(album["rev"], info["rev"])
        album["songs"].append({
            "file": item["file"], "title": info["title"], "artists": info["artists"],
            "track": info["track"], "missing": missing, "staged": not key,
            "album": info["album"], "albumArtist": info["album_artist"], "date": info["date"],
            "hasCover": info["has_cover"], "rev": info["rev"],
        })

    if stale:
        _remove(lambda i: (i["dir"], i["file"]) in stale)

    result = list(albums.values())
    for a in result:
        a["songs"].sort(key=lambda s: s["track"])
    result.sort(key=lambda a: (not a["staged"], a["album"].lower()))
    return result


def cover(dir_name: str, filename: str | None = None) -> tuple[bytes, str] | None:
    """Cover of the given song, or of the first pending song in the folder."""
    with _lock:
        items = [i for i in _load() if i["dir"] == dir_name and (filename is None or i["file"] == filename)]
    for item in items:
        if item.get("missing"):
            try:
                with open(_missing_cover_path(item["dir"], item["file"]), "rb") as f:
                    data = f.read()
            except OSError:
                continue
            return data, _mime(data)
        try:
            covr = MP4(_abs(item["dir"], item["file"])).tags.get("covr")
        except Exception:
            continue
        if covr:
            return bytes(covr[0]), _mime(bytes(covr[0]))
    return None


def audio_path(dir_name: str, filename: str) -> str:
    entry = _find(dir_name, filename)
    path = _abs(entry["dir"], entry["file"])
    if entry.get("missing") or not os.path.isfile(path):
        raise LookupError("Song has no audio")
    return path


def waveform(dir_name: str, filename: str, bars: int = 600) -> dict:
    """Duration and normalised peak amplitudes (0..1) for drawing the soundwave."""
    path = audio_path(dir_name, filename)
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=180)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise RuntimeError(f"ffmpeg failed: {e}")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-300:].decode(errors='replace')}")

    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    if not len(samples):
        raise RuntimeError("No audio data")
    step = max(1, len(samples) // bars)
    peaks = [max(map(abs, samples[i * step:(i + 1) * step]), default=0) for i in range(bars)]
    top = max(peaks) or 1
    return {"duration": MP4(path).info.length, "peaks": [round(p / top, 3) for p in peaks]}


def dismiss(dir_name: str | None = None, filename: str | None = None) -> int:
    """Mark songs as done without touching audio. Staged songs can't be dismissed."""
    return _remove(
        lambda i: bool(i["dir"])
        and (dir_name is None or i["dir"] == dir_name)
        and (filename is None or i["file"] == filename)
    )


def delete(dir_name: str, filename: str | None = None) -> None:
    """Delete one song, or the whole album folder, from disk and from pending."""
    if not dir_name:
        if not filename:
            raise ValueError("Invalid path")
        entry = _find("", filename)
        try:
            os.remove(_abs("", entry["file"]))
        except FileNotFoundError:
            pass
        _remove(lambda i: i["dir"] == "" and i["file"] == filename)
        return

    root = os.path.realpath(config.MUSIC_LIBRARY_DIR)
    album_dir = os.path.realpath(os.path.join(root, dir_name))
    target = os.path.realpath(os.path.join(album_dir, filename)) if filename else album_dir
    # Album must sit directly under the library root; a song directly inside the album.
    if os.path.dirname(album_dir) != root or (filename and os.path.dirname(target) != album_dir):
        raise ValueError("Invalid path")

    if filename:
        if os.path.isfile(target):
            os.remove(target)
    elif os.path.isdir(target):
        shutil.rmtree(target)
    _remove(lambda i: i["dir"] == dir_name and (filename is None or i["file"] == filename))


def _write_tags(audio: MP4, title, artists, album, album_artist, track, date, cover_data, clear_cover=False) -> None:
    audio["\u00a9nam"] = [title]
    if artists:
        audio["\u00a9ART"] = artists
    elif "\u00a9ART" in audio:
        del audio["\u00a9ART"]
    audio["\u00a9alb"] = [album]
    audio["aART"] = [album_artist]
    old_total = audio["trkn"][0][1] if audio.get("trkn") else 0
    audio["trkn"] = [(track, max(old_total, track))]
    audio["\u00a9day"] = [date]
    if cover_data:
        fmt = MP4Cover.FORMAT_PNG if cover_data[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(cover_data, imageformat=fmt)]
    elif clear_cover and "covr" in audio:
        del audio["covr"]


def edit(dir_name: str, filename: str, fields: dict, cover_data: bytes | None = None) -> None:
    """Apply metadata edits (and optional new cover); move the song if its album folder changes."""
    title = (fields.get("title") or "").strip()
    album = (fields.get("album") or "").strip()
    album_artist = (fields.get("album_artist") or "").strip()
    if not (title and album and album_artist):
        raise ValueError("Title, album and album artist are required")
    new_dir = downloader.make_directory_name(album, album_artist)
    if not new_dir:
        raise ValueError("Album and album artist need at least one letter or digit")
    try:
        track = max(0, int(fields.get("track") or 0))
    except (TypeError, ValueError):
        raise ValueError("Track must be a number")
    artists = [a.strip() for a in (fields.get("artists") or []) if a and a.strip()]
    date = (fields.get("date") or "").strip()
    clear_cover = bool(fields.get("clear_cover")) and not cover_data
    if cover_data and not (cover_data[:3] == b"\xff\xd8\xff" or cover_data[:4] == b"\x89PNG"):
        raise ValueError("Cover must be a JPEG or PNG image")

    with _op_lock:
        entry = _find(dir_name, filename)
        cur_dir, cur_file = entry["dir"], entry["file"]
        moving = new_dir != cur_dir
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        new_file = cur_file if cur_dir else f"{track:02d} - {safe_title}.m4a"

        if moving:
            dest = os.path.join(config.MUSIC_LIBRARY_DIR, new_dir, new_file)
            with _lock:
                taken = any(i["dir"] == new_dir and i["file"] == new_file for i in _load())
            if taken or os.path.exists(dest):
                raise ValueError("A song with that file name already exists in the target album")

        if entry.get("missing"):
            meta = dict(entry["meta"])
            meta.update(title=title, artists=artists, album=album, album_artist=album_artist,
                        track_number=track, date=date)
            old_cp, new_cp = _missing_cover_path(cur_dir, cur_file), _missing_cover_path(new_dir, new_file)
            if cover_data:
                os.makedirs(MISSING_COVER_DIR, exist_ok=True)
                with open(new_cp, "wb") as f:
                    f.write(cover_data)
                if old_cp != new_cp:
                    try:
                        os.remove(old_cp)
                    except OSError:
                        pass
            elif clear_cover:
                try:
                    os.remove(old_cp)
                except OSError:
                    pass
            elif old_cp != new_cp and os.path.exists(old_cp):
                os.replace(old_cp, new_cp)
            _update_entry(cur_dir, cur_file, dir=new_dir, meta=meta)
            return

        path = _abs(cur_dir, cur_file)
        audio = MP4(path)
        _write_tags(audio, title, artists, album, album_artist, track, date, cover_data, clear_cover)
        audio.save()

        if moving:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(path, dest)
            if cur_dir:
                try:
                    os.rmdir(os.path.dirname(path))
                except OSError:
                    pass
            _update_entry(cur_dir, cur_file, dir=new_dir, file=new_file)
            library.mark_downloaded(new_dir)


def _copy_tags(src_tags, dst_path: str) -> None:
    new = MP4(dst_path)
    new.delete()
    for key, value in (src_tags or {}).items():
        new[key] = value
    new.save()


def trim(dir_name: str, filename: str, start: float, end: float) -> None:
    """Cut the song to [start, end] seconds with ffmpeg, keeping tags and cover."""
    if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
        raise ValueError("Invalid trim range")

    with _op_lock:
        path = audio_path(dir_name, filename)
        old_tags = MP4(path).tags
        tmp_dir = tempfile.mkdtemp(prefix="trim-")
        try:
            out = os.path.join(tmp_dir, "out.m4a")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", path,
                   "-t", f"{end - start:.3f}", "-map", "0:a:0", "-c", "copy", out]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                raise RuntimeError(f"ffmpeg failed: {e}")
            if result.returncode != 0 or not os.path.exists(out):
                raise RuntimeError(f"ffmpeg failed: {result.stderr[-300:]}")
            _copy_tags(old_tags, out)
            shutil.move(out, path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def replace_audio(dir_name: str, filename: str, url: str) -> None:
    """Swap the song's audio for the given YouTube URL, keeping its tags and cover."""
    if not _is_allowed(url, ALLOWED_HOSTS):
        raise ValueError("Not a YouTube URL")

    entry = _find(dir_name, filename)
    missing = bool(entry.get("missing"))
    target = _abs(entry["dir"], entry["file"])
    old_tags = None if missing else MP4(target).tags

    tmp_dir = tempfile.mkdtemp(prefix="replace-")
    try:
        tmp_path = os.path.join(tmp_dir, "new.m4a")
        if not downloader._download_track(url, tmp_path):
            raise RuntimeError("Download failed")

        if missing:
            cover_data = None
            try:
                with open(_missing_cover_path(entry["dir"], entry["file"]), "rb") as f:
                    cover_data = f.read()
            except OSError:
                pass
            downloader._tag_m4a(filepath=tmp_path, cover_data=cover_data, **entry["meta"])
        else:
            _copy_tags(old_tags, tmp_path)

        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(tmp_path, target)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Staged songs stay pending until they are filed into an album.
    if entry["dir"]:
        _remove(lambda i: i["dir"] == entry["dir"] and i["file"] == entry["file"])
