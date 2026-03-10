import logging
import re
from urllib.parse import urlparse, parse_qs

from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)


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
