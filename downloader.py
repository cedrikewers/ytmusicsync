import logging
import os
import re
import subprocess
import tempfile

import requests
from mutagen.mp4 import MP4, MP4Cover
from unidecode import unidecode

import config
import library

logger = logging.getLogger(__name__)

# --- Multi-artist extraction from song titles ---

_FEAT_BRACKET_RE = re.compile(
    r'[\(\[]\s*(?:feat\.?|featuring|ft\.?|with)\s+(.+?)\s*[\)\]]',
    re.IGNORECASE,
)
_FEAT_BARE_RE = re.compile(
    r'\s+(?:feat\.?|featuring|ft\.?|with)\s+(.+?)\s*$',
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r'\s*(?:,|&|\band\b)\s*')


def _extract_featured_artists(title: str) -> list[str]:
    """Parse featured / collaborating artist names out of a song title."""
    artists: list[str] = []
    # Bracketed: "Song (feat. A & B)", "Song [ft. A, B and C]"
    for m in _FEAT_BRACKET_RE.finditer(title):
        artists.extend(p for p in _SPLIT_RE.split(m.group(1)) if p)
    # Bare (no brackets): "Song feat. A & B" — only if no bracket match
    if not artists:
        m = _FEAT_BARE_RE.search(title)
        if m:
            artists.extend(p for p in _SPLIT_RE.split(m.group(1)) if p)
    return [a.strip() for a in artists if a.strip()]


def _collect_all_artists(track_artists: list[str], title: str) -> list[str]:
    """Merge API-provided artists with any extra names parsed from the title.

    Deduplicates case-insensitively while preserving the original casing
    of the first occurrence.
    """
    seen: dict[str, str] = {}  # lowercase -> original
    for name in track_artists:
        key = name.strip().lower()
        if key and key not in seen:
            seen[key] = name.strip()

    for name in _extract_featured_artists(title):
        key = name.lower()
        if key and key not in seen:
            seen[key] = name

    return list(seen.values()) or track_artists or ["Unknown Artist"]


def make_directory_name(album_title: str, artist_name: str) -> str:
    """Generate the Jellyfin-compatible directory name.

    Same scheme as the old Spotify script:
    asciify album+artist, remove all non-alphanumeric chars.
    """
    raw = album_title + artist_name
    return "".join(c for c in unidecode(raw) if c.isalnum())


def download_album(album_info: dict, yt_client=None, progress_cb=None, force: bool = False) -> bool:
    """Download all tracks of an album to the Jellyfin library.

    Args:
        album_info: dict from ytmusic_client.get_album()
        yt_client: YTMusic instance (for lyrics lookup)
        progress_cb: optional callable(message: str) for progress updates
        force: if True, skip the is_downloaded check (for manual downloads)

    Returns True if anything was downloaded.
    """
    from ytmusic_client import get_lyrics, get_release_date, best_thumbnail_url

    album_title = album_info["title"]
    album_artist = album_info.get("albumArtist", "Unknown Artist")
    artists_list = album_info.get("artists", [album_artist])
    year = album_info.get("year", "")
    tracks = album_info.get("tracks", [])
    track_count = album_info.get("trackCount", len(tracks))
    thumbnails = album_info.get("thumbnails", [])

    dir_name = make_directory_name(album_title, album_artist)

    if not force and library.is_downloaded(dir_name):
        logger.info("Already downloaded: %s", dir_name)
        return False

    dest_dir = os.path.join(config.MUSIC_LIBRARY_DIR, dir_name)
    os.makedirs(dest_dir, exist_ok=True)

    # Download cover image once
    cover_url = best_thumbnail_url(thumbnails)
    cover_data = None
    if cover_url:
        try:
            resp = requests.get(cover_url, timeout=30)
            resp.raise_for_status()
            cover_data = resp.content
        except Exception:
            logger.warning("Failed to download cover for %s", album_title)

    msg = f"Downloading {album_title} by {album_artist} ({len(tracks)} tracks)"
    logger.info(msg)
    if progress_cb:
        progress_cb(msg)

    any_downloaded = False

    for track in tracks:
        video_id = track.get("videoId")
        if not video_id:
            logger.warning("Skipping track with no videoId: %s", track.get("title"))
            continue

        if not track.get("isAvailable", True):
            logger.warning("Skipping unavailable track: %s", track.get("title"))
            continue

        track_title = track.get("title", "Unknown")
        track_number = track.get("trackNumber", 0)
        track_artists = track.get("artists", artists_list)
        all_artists = _collect_all_artists(track_artists, track_title)

        # yt-dlp output template: TrackNumber - Title.m4a
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in track_title)
        out_filename = f"{track_number:02d} - {safe_title}.m4a"
        out_path = os.path.join(dest_dir, out_filename)

        if os.path.exists(out_path):
            logger.debug("File already exists, skipping: %s", out_path)
            any_downloaded = True
            continue

        # Download with yt-dlp
        if not _download_track(video_id, out_path):
            continue

        any_downloaded = True

        # Get lyrics and precise release date
        lyrics = None
        release_date_str = ""
        if yt_client:
            lyrics = get_lyrics(yt_client, video_id)
            rd = get_release_date(yt_client, video_id)
            if rd:
                release_date_str = rd.strftime("%Y-%m-%d")
        if not release_date_str:
            release_date_str = f"{year}-01-01" if year and len(year) == 4 else (year or "")

        # Tag the file
        _tag_m4a(
            filepath=out_path,
            title=track_title,
            artists=all_artists,
            album=album_title,
            album_artist=album_artist,
            track_number=track_number,
            track_total=track_count,
            disc_number=1,
            disc_total=1,
            date=release_date_str,
            lyrics=lyrics,
            cover_data=cover_data,
        )

    if any_downloaded:
        library.mark_downloaded(dir_name)
        msg = f"Completed: {album_title} by {album_artist}"
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    return any_downloaded


def download_single_song(video_id: str, album_info: dict, yt_client=None, progress_cb=None, force: bool = False) -> bool:
    """Download a single song given its videoId and album context.

    album_info should come from ytmusic_client.get_album() or get_song_album_info().
    The specific track matching video_id will be downloaded.
    """
    tracks = album_info.get("tracks", [])
    target = None
    for t in tracks:
        if t.get("videoId") == video_id:
            target = t
            break

    if target is None:
        # If the song isn't found in the track list, download the whole album
        logger.info("Song %s not matched in album tracks, downloading full album", video_id)
        return download_album(album_info, yt_client, progress_cb, force=force)

    # Build a single-track album_info to reuse download_album
    single_info = dict(album_info)
    single_info["tracks"] = [target]
    single_info["trackCount"] = album_info.get("trackCount", 1)
    return download_album(single_info, yt_client, progress_cb, force=force)


def _download_track(video_id: str, out_path: str) -> bool:
    """Download a single track using yt-dlp. Returns True on success."""
    url = f"https://music.youtube.com/watch?v={video_id}"

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "m4a",
        "--audio-quality", "0",
        "--no-playlist",
        "--output", out_path,
    ]

    if config.COOKIE_FILE and os.path.exists(config.COOKIE_FILE):
        cmd.extend(["--cookies", config.COOKIE_FILE])

    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.error("yt-dlp failed for %s: %s", video_id, result.stderr[-500:] if result.stderr else "")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timed out for %s", video_id)
        return False
    except FileNotFoundError:
        logger.error("yt-dlp not found. Make sure it's installed and in PATH.")
        return False


def _tag_m4a(
    filepath: str,
    title: str,
    artists: list[str],
    album: str,
    album_artist: str,
    track_number: int,
    track_total: int,
    disc_number: int,
    disc_total: int,
    date: str,
    lyrics: str | None,
    cover_data: bytes | None,
) -> None:
    """Write ID3-style tags to an M4A file using mutagen."""
    try:
        audio = MP4(filepath)
    except Exception:
        logger.error("Failed to open %s for tagging", filepath)
        return

    audio["\u00a9nam"] = [title]                          # Title
    audio["\u00a9ART"] = artists                              # Artist (one tag per artist)
    audio["\u00a9alb"] = [album]                          # Album
    audio["aART"] = [album_artist]            # Album Artist
    audio["trkn"] = [(track_number, track_total)]  # Track Number
    audio["disk"] = [(disc_number, disc_total)]    # Disc Number
    audio["\u00a9day"] = [date]               # Date

    if lyrics:
        audio["\u00a9lyr"] = [lyrics]         # Lyrics

    if cover_data:
        # Detect image format
        if cover_data[:4] == b"\x89PNG":
            img_format = MP4Cover.FORMAT_PNG
        else:
            img_format = MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(cover_data, imageformat=img_format)]

    try:
        audio.save()
    except Exception:
        logger.error("Failed to save tags for %s", filepath)
