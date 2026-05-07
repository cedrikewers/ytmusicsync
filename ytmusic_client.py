import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)


def _is_audio_video_type(video_type: str | None) -> bool:
    """Return True for audio/studio track entries from YT Music."""
    if not video_type:
        return True
    return "ATV" in video_type.upper()


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (title or "").lower())).strip()


def _clean_title_for_song_search(title: str, artist: str | None = None) -> str:
    """Conservative cleanup for MV-like suffixes in song titles."""
    t = (title or "").strip()

    # Remove common trailing MV markers but keep core title tokens intact.
    t = re.sub(r"\s*(official\s+)?(music\s+video|mv)\s*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\[[^\]]*\]\s*$", "", t)
    t = re.sub(r"\s*\([^\)]*\)\s*$", "", t)
    t = t.strip(" '\"-_")
    return t or title


def _find_best_studio_song_id_by_search(yt: YTMusic, title: str, artist: str) -> str | None:
    """Search song catalog and pick best candidate for studio/audio version."""
    cleaned = _clean_title_for_song_search(title, artist)
    query_candidates = [
        f"{title} {artist}".strip(),
        f"{cleaned} {artist}".strip(),
    ]

    seen_ids: set[str] = set()
    candidates: list[dict] = []

    for query in query_candidates:
        if not query:
            continue
        try:
            results = yt.search(query, filter="songs", limit=20)
        except Exception:
            logger.debug("Song search failed for query %s", query)
            continue

        for r in results:
            rid = r.get("videoId")
            if not rid or rid in seen_ids:
                continue
            seen_ids.add(rid)
            candidates.append(r)

    if not candidates:
        return None

    wanted_artist = _norm_title(artist)
    wanted_title_variants = {
        _norm_title(title),
        _norm_title(cleaned),
    }
    wanted_title_variants.discard("")

    best_id = None
    best_score = -1

    for r in candidates:
        rid = r.get("videoId")
        if not rid:
            continue

        # Validate candidate as audio/studio from canonical song metadata.
        try:
            song = yt.get_song(rid)
            details = song.get("videoDetails", {})
            vtype = (details.get("musicVideoType") or "").upper()
            if not _is_audio_video_type(vtype):
                continue
            c_title = _norm_title(details.get("title", "") or r.get("title", ""))
            c_artist = _norm_title(details.get("author", ""))
        except Exception:
            c_title = _norm_title(r.get("title", ""))
            artists = [_norm_title(a.get("name", "")) for a in r.get("artists", []) if a.get("name")]
            c_artist = artists[0] if artists else ""

        score = 0
        if c_title in wanted_title_variants:
            score += 4
        elif any(v and v in c_title for v in wanted_title_variants):
            score += 1

        if wanted_artist and c_artist == wanted_artist:
            score += 4
        elif wanted_artist and wanted_artist in c_artist:
            score += 2

        if score > best_score:
            best_score = score
            best_id = rid

    return best_id if best_score >= 4 else None


def _ensure_album_contains_track(album_info: dict, video_id: str, title: str, artist: str) -> dict:
    """Ensure album_info has a track with video_id so single-song download can match it."""
    updated = dict(album_info)
    tracks = [dict(t) for t in album_info.get("tracks", [])]

    if any(t.get("videoId") == video_id for t in tracks):
        updated["tracks"] = tracks
        return updated

    wanted = _norm_title(_clean_title_for_song_search(title, artist))
    replace_idx = None
    for i, t in enumerate(tracks):
        if _norm_title(t.get("title", "")) == wanted:
            replace_idx = i
            break

    new_track = {
        "videoId": video_id,
        "title": title or (tracks[replace_idx].get("title") if replace_idx is not None else "Unknown"),
        "artists": [artist] if artist else (tracks[replace_idx].get("artists", []) if replace_idx is not None else []),
        "trackNumber": tracks[replace_idx].get("trackNumber", 1) if replace_idx is not None else 1,
        "duration_seconds": tracks[replace_idx].get("duration_seconds", 0) if replace_idx is not None else 0,
        "isAvailable": True,
        "videoType": "MUSIC_VIDEO_TYPE_ATV",
        "creditsBrowseId": tracks[replace_idx].get("creditsBrowseId") if replace_idx is not None \
            else (tracks[0].get("creditsBrowseId") if tracks and len(tracks) == 1 \
            else None)
    }

    if replace_idx is not None:
        tracks[replace_idx] = new_track
    else:
        tracks.insert(0, new_track)

    updated["tracks"] = tracks
    return updated


def _pick_best_album_track_video_id(album_tracks: list[dict], requested_title: str) -> str | None:
    """Pick the best matching audio/studio track videoId from album tracks."""
    if not album_tracks:
        return None

    audio_tracks = [t for t in album_tracks if _is_audio_video_type(t.get("videoType"))]
    candidates = audio_tracks or album_tracks
    wanted = _norm_title(requested_title)

    if wanted:
        for t in candidates:
            if _norm_title(t.get("title", "")) == wanted and t.get("videoId"):
                return t["videoId"]

    for t in candidates:
        if t.get("videoId"):
            return t["videoId"]

    return None


def create_client() -> YTMusic:
    return YTMusic()


def search_artist(yt: YTMusic, name: str) -> list[dict]:
    """Search for artists by name. Returns list of {name, channelId, thumbnails}."""
    results = yt.search(name, filter="artists", limit=5)
    return [
        {
            "name": r.get("artist", ""),
            "channelId": r.get("browseId", ""),
            "thumbnails": r.get("thumbnails", []),
        }
        for r in results
        if r.get("browseId")
    ]


def get_artist_releases(yt: YTMusic, channel_id: str) -> list[dict]:
    """Get all albums and singles for an artist.

    Returns list of dicts with keys: browseId, title, type, year, thumbnails.
    """
    artist = yt.get_artist(channel_id)
    releases = []

    # Albums from the artist page (limited set)
    for section_key in ("albums", "singles"):
        section = artist.get(section_key, {})
        browse_id = section.get("browseId")
        results = section.get("results", [])

        if browse_id:
            # Fetch full list
            try:
                full = yt.get_artist_albums(browse_id, params=None)
                results = full
            except Exception:
                logger.warning("Failed to fetch full %s for %s", section_key, channel_id)

        for item in results:
            # Normalise year: on artist page albums have year in 'type' field,
            # on get_artist_albums it's in 'year'
            year = item.get("year") or item.get("type")
            release_type = item.get("type") if item.get("year") else section_key.rstrip("s")
            releases.append(
                {
                    "browseId": item.get("browseId", ""),
                    "title": item.get("title", ""),
                    "type": release_type or "album",
                    "year": year,
                    "thumbnails": item.get("thumbnails", []),
                }
            )

    return releases


def get_album(yt: YTMusic, browse_id: str) -> dict:
    """Get full album info including tracks.

    Returns dict with keys: title, artists, year, tracks, thumbnails, trackCount, type.
    Each track has: videoId, title, artists, trackNumber, duration_seconds.
    """
    album = yt.get_album(browse_id)

    artists = album.get("artists", [])
    artist_name = artists[0]["name"] if artists else "Unknown Artist"

    tracks = []
    for t in album.get("tracks", []):
        tracks.append(
            {
                "videoId": t.get("videoId"),
                "title": t.get("title", ""),
                "artists": [a.get("name", "") for a in t.get("artists", [])],
                "trackNumber": t.get("trackNumber", 0),
                "duration_seconds": t.get("duration_seconds", 0),
                "isAvailable": t.get("isAvailable", True),
                "videoType": t.get("videoType"),
                "creditsBrowseId": t.get("creditsBrowseId"),
            }
        )

    # Best thumbnail (largest)
    thumbnails = album.get("thumbnails", [])

    return {
        "title": album.get("title", ""),
        "artists": [a.get("name", "") for a in artists],
        "albumArtist": artist_name,
        "year": album.get("year", ""),
        "type": album.get("type", "Album"),
        "tracks": tracks,
        "trackCount": album.get("trackCount", len(tracks)),
        "thumbnails": thumbnails,
        "audioPlaylistId": album.get("audioPlaylistId"),
    }


def get_lyrics(yt: YTMusic, video_id: str) -> str | None:
    """Try to get lyrics for a song. Returns lyrics text or None."""
    try:
        watch = yt.get_watch_playlist(video_id)
        lyrics_browse_id = watch.get("lyrics")
        if not lyrics_browse_id:
            return None
        lyrics_data = yt.get_lyrics(lyrics_browse_id)
        return lyrics_data.get("lyrics")
    except Exception:
        logger.debug("Could not get lyrics for %s", video_id)
        return None


def get_release_date(yt: YTMusic, video_id: str) -> datetime | None:
    """Get the publish date of a song via get_song() microformat.

    Returns a timezone-aware datetime, or None on failure.
    """
    try:
        song = yt.get_song(video_id)
        mf = song.get("microformat", {}).get("microformatDataRenderer", {})
        date_str = mf.get("publishDate") or mf.get("uploadDate")
        if not date_str:
            return None
        return datetime.fromisoformat(date_str)
    except Exception:
        logger.debug("Could not get release date for %s", video_id)
        return None


def best_thumbnail_url(thumbnails: list[dict]) -> str | None:
    """Pick the highest-resolution thumbnail URL."""
    if not thumbnails:
        return None
    # Sort by width descending, pick largest
    by_size = sorted(thumbnails, key=lambda t: t.get("width", 0), reverse=True)
    return by_size[0].get("url")


# --- URL parsing helpers ---

_YTM_ALBUM_RE = re.compile(
    r"music\.youtube\.com/(?:browse|playlist\?list=OLAK5uy_[a-zA-Z0-9_-]+|playlist\?list=)(MPREb_[a-zA-Z0-9_-]+|OLAK5uy_[a-zA-Z0-9_-]+)"
)
_YTM_SONG_RE = re.compile(
    r"music\.youtube\.com/watch\?v=([a-zA-Z0-9_-]+)"
)


def parse_url(url: str) -> dict | None:
    """Parse a YouTube Music URL into {type: 'album'|'song', id: ...}.

    Supported:
      - music.youtube.com/watch?v=VIDEO_ID  -> song
      - music.youtube.com/browse/MPREb_...  -> album (browseId)
      - music.youtube.com/playlist?list=OLAK5uy_... -> album (playlistId)
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if "music.youtube.com" not in host:
        return None

    path = parsed.path
    query = parse_qs(parsed.query)

    # Song: /watch?v=...
    if path == "/watch" and "v" in query:
        return {"type": "song", "id": query["v"][0]}

    # Album browse: /browse/MPREb_...
    if path.startswith("/browse/"):
        browse_id = path.split("/browse/", 1)[1].split("?")[0]
        if browse_id:
            return {"type": "album", "id": browse_id}

    # Playlist: /playlist?list=OLAK5uy_...
    if path == "/playlist" and "list" in query:
        playlist_id = query["list"][0]
        # OLAK5uy_ playlists are album audio playlists
        return {"type": "playlist", "id": playlist_id}

    return None


def get_album_browse_id_from_playlist(yt: YTMusic, playlist_id: str) -> str | None:
    """Convert an OLAK5uy_ audio playlist ID to an album browseId."""
    try:
        return yt.get_album_browse_id(playlist_id)
    except Exception:
        logger.debug("Could not resolve playlist %s to album", playlist_id)
        return None


def get_song_album_info(yt: YTMusic, video_id: str) -> dict | None:
    """Get album info for a single song via its videoId.

    Returns a dict with album metadata plus a single-element tracks list.
    """
    watch = yt.get_watch_playlist(video_id)
    if not watch or not watch.get("tracks"):
        return None

    track = watch["tracks"][0]
    album_data = track.get("album")
    if not album_data:
        return None

    browse_id = album_data.get("id")
    if browse_id:
        return get_album(yt, browse_id)

    return None


def resolve_song_download_target(yt: YTMusic, video_id: str) -> dict | None:
    """Resolve a song URL videoId to an album and preferred studio/audio videoId.

    This handles grouped watch pages where the requested id can be a music-video
    variant while an album audio track exists.
    """
    try:
        song = yt.get_song(video_id)
    except Exception:
        logger.debug("Could not load song metadata for %s", video_id)
        return None

    video_details = song.get("videoDetails", {})
    requested_title = video_details.get("title", "")
    requested_artist = video_details.get("author", "")
    requested_type = (video_details.get("musicVideoType") or "").upper()

    selected_video_id = video_id
    if requested_type and not _is_audio_video_type(requested_type):
        found = _find_best_studio_song_id_by_search(yt, requested_title, requested_artist)
        if found:
            selected_video_id = found

    selected_title = requested_title
    selected_artist = requested_artist
    if selected_video_id != video_id:
        try:
            selected_song = yt.get_song(selected_video_id)
            selected_details = selected_song.get("videoDetails", {})
            selected_title = selected_details.get("title", selected_title)
            selected_artist = selected_details.get("author", selected_artist)
        except Exception:
            logger.debug("Could not load selected song metadata for %s", selected_video_id)

    album_info = get_song_album_info(yt, selected_video_id)
    if album_info:
        album_info = _ensure_album_contains_track(album_info, selected_video_id, selected_title, selected_artist)
        return {"albumInfo": album_info, "videoId": selected_video_id}

    try:
        watch = yt.get_watch_playlist(selected_video_id)
    except Exception:
        logger.debug("Could not load watch playlist for %s", selected_video_id)
        return None

    tracks = watch.get("tracks") or []
    if not tracks:
        return None

    requested_track = None
    preferred_track = None
    fallback_track = None

    for t in tracks:
        tid = t.get("videoId")
        album_data = t.get("album") or {}
        album_id = album_data.get("id")
        if not tid or not album_id:
            continue

        if tid == selected_video_id and requested_track is None:
            requested_track = t

        if fallback_track is None:
            fallback_track = t

        if _is_audio_video_type(t.get("videoType")):
            preferred_track = t
            break

    selected = preferred_track or requested_track or fallback_track
    if not selected:
        return None

    browse_id = (selected.get("album") or {}).get("id")
    if not browse_id:
        return None

    album_info = get_album(yt, browse_id)
    album_tracks = album_info.get("tracks", [])

    requested_title = (requested_track or selected).get("title", "")
    selected_video_id = selected.get("videoId") or selected_video_id
    album_track = next((t for t in album_tracks if t.get("videoId") == selected_video_id), None)

    if album_track is None:
        album_info = _ensure_album_contains_track(album_info, selected_video_id, selected_title, selected_artist)
        return {"albumInfo": album_info, "videoId": selected_video_id}

    if not _is_audio_video_type(album_track.get("videoType")):
        best_video_id = _pick_best_album_track_video_id(album_tracks, requested_title)
        if best_video_id:
            selected_video_id = best_video_id

    album_info = _ensure_album_contains_track(album_info, selected_video_id, selected_title, selected_artist)
    return {"albumInfo": album_info, "videoId": selected_video_id}
