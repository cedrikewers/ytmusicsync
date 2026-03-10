# YT-Rewrite: YouTube Music → Jellyfin Bridge

## Overview

Replace the Spotify-based download pipeline with YouTube Music (via `ytmusicapi` + `yt-dlp`).
Merge the old `SpotifyToJellyfin` artist-monitoring service and the `WebInterface` manual-download
web UI into **one Docker-based service**.

---

## Architecture

```
yt-rewrite/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── config.py              # All configuration (env vars, paths, intervals)
├── main.py                # Entrypoint – starts scheduler + Flask/SocketIO web server
├── scheduler.py           # APScheduler periodic job: check artists for new releases
├── downloader.py          # yt-dlp download + mutagen ID3/M4A tagging logic
├── ytmusic_client.py      # ytmusicapi wrapper: search artists, get albums, songs, lyrics
├── library.py             # Tracks what has already been downloaded (ignore list / DB)
├── artists.json           # List of followed artists (YTMusic channel IDs + names)
├── templates/
│   └── index.jinja        # Web UI (adapted from old WebInterface)
└── TODO.md                # This file
```

---

## Detailed Task Breakdown

### Phase 1 – Project Skeleton & Config
- [x] Create directory structure inside `yt-rewrite/`
- [x] Create `requirements.txt` (ytmusicapi, yt-dlp, mutagen, flask, flask-socketio, apscheduler, unidecode)
- [x] Create `config.py` – read env vars:
  - `MUSIC_LIBRARY_DIR` (Jellyfin music root, default `/music`)
  - `CHECK_INTERVAL_HOURS` (default `6`)
  - `LOOKBACK_DAYS` (how far back to check for new releases, default `3`)
  - `COOKIE_FILE` (optional path to `music.youtube.com_cookies.txt`)
  - `YTMUSIC_AUTH` (path to ytmusicapi auth headers file, optional)
  - `WEB_PORT` (default `5023`)
- [x] Migrate `artists.json` to new format: `[{"name": "ARTIST", "channelId": "UC..."}]`
  - Provide a helper script/command to search & add artists by name

### Phase 2 – YouTube Music Client (`ytmusic_client.py`)
- [x] Initialize `ytmusicapi.YTMusic()` (unauthenticated or with browser auth)
- [x] `get_artist_albums(channelId) -> list[Album]` – returns albums/singles/EPs
- [x] `get_album_tracks(browseId) -> list[Track]` with metadata (title, artist, track#, disc#, date)
- [x] `get_lyrics(videoId) -> str | None`
- [x] `search_artist(name) -> list[ArtistResult]` for the web UI / artist management
- [x] `get_album_info(browseId)` – full album metadata including cover URL
- [x] Parse URL helpers: extract album/playlist/song IDs from `music.youtube.com` URLs

### Phase 3 – Downloader & Tagger (`downloader.py`)
- [x] Directory naming: `"".join(c for c in unidecode(album_title + artist_name) if c.isalnum())`
  (preserves existing Jellyfin library compatibility)
- [x] Download with `yt-dlp`:
  - Format: m4a (best audio, m4a container)
  - Use cookie file if provided
  - Output template places files in the correct Jellyfin directory
- [x] Tag with `mutagen` (MP4/M4A tags):
  - `©nam` – Title
  - `©ART` – Artist
  - `©alb` – Album
  - `trkn` – Track Number (tuple: track, total)
  - `disk` – Disc Number (tuple: disc, total)
  - `©day` – Date (YYYY-MM-DD)
  - `aART` – Album Artist
  - `©lyr` – Lyrics
  - `covr` – Cover image (download from URL, embed as MP4Cover)
- [x] Return download result with status for web UI feedback

### Phase 4 – Library / Ignore Tracking (`library.py`)
- [x] Load/save an `ignore.txt` (or JSON) of already-downloaded directory names
- [x] `is_downloaded(directory_name) -> bool`
- [x] `mark_downloaded(directory_name)`
- [x] On startup, also scan filesystem to rebuild ignore list if needed

### Phase 5 – Scheduler (`scheduler.py`)
- [x] Use `APScheduler` `BackgroundScheduler`
- [x] On startup: run a full check immediately
- [x] Then every `CHECK_INTERVAL_HOURS`: for each artist in `artists.json`:
  1. Fetch their albums/singles via `ytmusic_client.get_artist_albums()`
  2. Filter to releases within `LOOKBACK_DAYS`
  3. For each new release not in ignore list → download & tag all tracks
- [x] Log all actions (new releases found, downloads started/completed, errors)

### Phase 6 – Web Interface (`main.py` + `templates/index.jinja`)
- [x] Flask + Flask-SocketIO (keep existing WS-based progress/log pattern)
- [x] Accept YouTube Music URLs:
  - `music.youtube.com/watch?v=...` (single song)
  - `music.youtube.com/browse/...` or `music.youtube.com/playlist?list=...` (album/playlist)
- [x] URL regex updated for YouTube Music instead of Spotify
- [x] Download queue with WebSocket progress updates (same UX as before)
- [x] Live log tailing via WebSocket (same pattern)
- [x] Update placeholder text and branding in the template

### Phase 7 – Docker
- [x] `Dockerfile`:
  - Base: `python:3.14-slim`
  - Install ffmpeg (needed by yt-dlp for m4a muxing)
  - Copy code, install requirements
  - Volume mount for `/music` (Jellyfin library) and `/data` (artists.json, ignore.txt, cookies)
  - Entrypoint: `python main.py`
- [x] `docker-compose.yml`:
  - Service with volume mounts, port mapping, env vars
  - Restart policy

### Phase 8 – Testing & Migration
- [ ] Manual test: add an artist, trigger check, verify download + tags
- [ ] Verify Jellyfin picks up the new files (directory naming compatible)
- [ ] Migrate existing `artists.json` from Spotify format to YTMusic format
  - Need to search each artist name on YTMusic and get their channelId
- [ ] Test web UI: paste a YTMusic album URL, verify download

---

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Audio format | m4a | Matches existing library; native yt-dlp format from YouTube |
| Tagging library | mutagen | Handles MP4/M4A natively; no external deps |
| Scheduler | APScheduler | Lightweight, in-process, no external broker needed |
| Web framework | Flask + SocketIO | Matches existing web UI; proven pattern |
| Container base | python:3.14-slim | Matches .venv Python version |
| Auth | Unauthenticated ytmusicapi | No login needed for public artist/album data |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MUSIC_LIBRARY_DIR` | `/music` | Jellyfin music library root |
| `CHECK_INTERVAL_HOURS` | `6` | Hours between automatic artist checks |
| `LOOKBACK_DAYS` | `3` | How many days back to look for new releases |
| `COOKIE_FILE` | (none) | Path to YouTube cookies file for yt-dlp |
| `WEB_PORT` | `5023` | Web interface port |
| `LOG_LEVEL` | `INFO` | Python logging level |
