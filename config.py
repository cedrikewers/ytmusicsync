import os

# Jellyfin music library root
MUSIC_LIBRARY_DIR = os.environ.get("MUSIC_LIBRARY_DIR", "/music")

# How often to check for new releases (hours)
CHECK_INTERVAL_HOURS = int(os.environ.get("CHECK_INTERVAL_HOURS", "6"))

# How far back to look for new releases (days)
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "3"))

# Optional YouTube cookies file for yt-dlp (age-restricted content, etc.)
COOKIE_FILE = os.environ.get("COOKIE_FILE", "")

# Data directory for persistent state (artists.json, ignore.txt)
DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))

# Web interface port
WEB_PORT = int(os.environ.get("WEB_PORT", "5023"))

# Logging level
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Paths derived from DATA_DIR
ARTISTS_FILE = os.path.join(DATA_DIR, "artists.json")
IGNORE_FILE = os.path.join(DATA_DIR, "ignore.txt")

# URL prefix for reverse proxy (e.g. "/ytdl")
_raw_prefix = os.environ.get("URL_PREFIX", "").strip("/")
URL_PREFIX = f"/{_raw_prefix}" if _raw_prefix else ""
