import json
import logging
import os
from threading import Lock

import config

logger = logging.getLogger(__name__)

_lock = Lock()


def _ignore_path() -> str:
    return config.IGNORE_FILE


def load_ignore_set() -> set[str]:
    path = _ignore_path()
    if not os.path.exists(path):
        return set()
    with open(path, "r") as f:
        return {line.strip() for line in f if line.strip()}


def is_downloaded(directory_name: str) -> bool:
    with _lock:
        return directory_name in load_ignore_set()


def mark_downloaded(directory_name: str) -> None:
    with _lock:
        path = _ignore_path()
        existing = load_ignore_set()
        if directory_name in existing:
            return
        with open(path, "a") as f:
            f.write(directory_name + "\n")


def rebuild_from_filesystem() -> int:
    """Scan MUSIC_LIBRARY_DIR and add any existing directories to ignore list."""
    music_dir = config.MUSIC_LIBRARY_DIR
    if not os.path.isdir(music_dir):
        return 0

    existing = load_ignore_set()
    added = 0
    for entry in os.listdir(music_dir):
        if os.path.isdir(os.path.join(music_dir, entry)):
            if entry not in existing:
                mark_downloaded(entry)
                added += 1

    if added:
        logger.info("Rebuilt ignore list: added %d directories from filesystem", added)
    return added


# --- artists.json management ---

def load_artists() -> list[dict]:
    """Load artists list. Each entry: {name, channelId}."""
    path = config.ARTISTS_FILE
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def save_artists(artists: list[dict]) -> None:
    path = config.ARTISTS_FILE
    with open(path, "w") as f:
        json.dump(artists, f, indent=2, ensure_ascii=False)


def add_artist(name: str, channel_id: str) -> bool:
    """Add an artist if not already present. Returns True if added."""
    artists = load_artists()
    for a in artists:
        if a["channelId"] == channel_id:
            return False
    artists.append({"name": name, "channelId": channel_id})
    save_artists(artists)
    return True


def remove_artist(channel_id: str) -> bool:
    """Remove an artist by channelId. Returns True if removed."""
    artists = load_artists()
    new = [a for a in artists if a["channelId"] != channel_id]
    if len(new) == len(artists):
        return False
    save_artists(new)
    return True
