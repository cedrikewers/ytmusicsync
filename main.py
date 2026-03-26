import json
import logging
import os
import re
import time
from collections import deque
from queue import Queue, Empty
from threading import Lock, Thread

from flask import Flask, Blueprint, request, jsonify, render_template
from flask_sock import Sock

import config
import downloader
import library
import scheduler
import ytmusic_client

# --- Logging setup ---
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(config.DATA_DIR, "app.log"), encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)

# --- Flask app ---
app = Flask(__name__)
bp = Blueprint("main", __name__)
sock = Sock(app)

# Download queue state
progress_dict: dict[str, str] = {}
queue_lock = Lock()
download_lock = Lock()

# --- WebSocket client management ---
# Each connected client gets a Queue; broadcasting pushes to all queues.
_ws_clients: dict[int, Queue] = {}
_ws_clients_lock = Lock()
_next_client_id = 0


def _register_ws_client() -> tuple[int, Queue]:
    global _next_client_id
    q: Queue[str] = Queue()
    with _ws_clients_lock:
        cid = _next_client_id
        _next_client_id += 1
        _ws_clients[cid] = q
    return cid, q


def _unregister_ws_client(cid: int) -> None:
    with _ws_clients_lock:
        _ws_clients.pop(cid, None)


def ws_broadcast(event: str, data: dict) -> None:
    """Send a JSON message to every connected WebSocket client."""
    msg = json.dumps({"event": event, **data})
    with _ws_clients_lock:
        for q in _ws_clients.values():
            q.put_nowait(msg)


# --- Live log tailing ---
LOG_FILE = os.path.join(config.DATA_DIR, "app.log")
_tail_task_started = False
_tail_lock = Lock()
_last_lines_cache: deque[str] = deque(maxlen=200)


def _tail_log_forever():
    fh = None
    last_inode = None
    position = 0

    def open_file(seek_end=True):
        nonlocal fh, last_inode, position
        if fh:
            try:
                fh.close()
            except Exception:
                pass
        try:
            fh = open(LOG_FILE, "r", encoding="utf-8", errors="replace")
            if seek_end:
                fh.seek(0, os.SEEK_END)
            position = fh.tell()
            try:
                last_inode = os.fstat(fh.fileno()).st_ino
            except Exception:
                last_inode = None
        except FileNotFoundError:
            fh = None

    # Preload last lines
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as preload:
            lines = preload.readlines()
            for line in lines[-200:]:
                _last_lines_cache.append(line.rstrip("\n"))
    except FileNotFoundError:
        pass

    open_file(seek_end=True)
    while True:
        try:
            if fh is None:
                time.sleep(0.5)
                open_file(seek_end=True)
                continue

            try:
                st = os.stat(LOG_FILE)
                if last_inode is not None and st.st_ino != last_inode:
                    open_file(seek_end=False)
                    continue
                if st.st_size < position:
                    open_file(seek_end=False)
                    continue
            except FileNotFoundError:
                open_file(seek_end=True)
                continue

            line = fh.readline()
            if not line:
                time.sleep(0.3)
                continue
            position += len(line.encode("utf-8", errors="replace"))
            line = line.rstrip("\n")
            _last_lines_cache.append(line)
            ws_broadcast("log", {"line": line})
        except Exception:
            time.sleep(0.5)


def _ensure_tail_started():
    global _tail_task_started
    with _tail_lock:
        if not _tail_task_started:
            t = Thread(target=_tail_log_forever, daemon=True)
            t.start()
            _tail_task_started = True


# --- WebSocket endpoint ---


@sock.route(config.URL_PREFIX + "/ws")
def ws_handler(ws):
    """Each client connects here. We send cached log lines, then relay
    broadcast messages from the per-client queue until disconnect."""
    _ensure_tail_started()

    cid, q = _register_ws_client()
    try:
        # Send cached log history
        for line in list(_last_lines_cache):
            ws.send(json.dumps({"event": "log", "line": line}))

        # Relay messages from broadcast queue
        while True:
            try:
                msg = q.get(timeout=1.0)
                ws.send(msg)
            except Empty:
                # Send a ping to detect broken connections
                try:
                    ws.send(json.dumps({"event": "ping"}))
                except Exception:
                    break
    except Exception:
        pass
    finally:
        _unregister_ws_client(cid)


# --- Routes ---


@bp.route("/", methods=["GET"])
def index():
    artists = library.load_artists()
    return render_template("index.jinja", artists=artists, prefix=config.URL_PREFIX)


def _download_url(url: str):
    """Background task to download a YouTube Music URL."""
    parsed = ytmusic_client.parse_url(url)
    if parsed is None:
        ws_broadcast("progress", {"url": url, "status": "Invalid URL"})
        return

    ws_broadcast("progress", {"url": url, "status": "Resolving..."})

    yt = ytmusic_client.create_client()

    def progress_cb(msg):
        ws_broadcast("progress", {"url": url, "status": msg})

    try:
        with download_lock:
            if parsed["type"] == "song":
                ws_broadcast("progress", {"url": url, "status": "Fetching song info..."})
                target = ytmusic_client.resolve_song_download_target(yt, parsed["id"])
                if target is None:
                    ws_broadcast("progress", {"url": url, "status": "Could not resolve song"})
                    return
                ws_broadcast("progress", {"url": url, "status": "Downloading..."})
                downloader.download_single_song(
                    target["videoId"],
                    target["albumInfo"],
                    yt_client=yt,
                    progress_cb=progress_cb,
                    force=True,
                )

            elif parsed["type"] == "album":
                ws_broadcast("progress", {"url": url, "status": "Fetching album info..."})
                album_info = ytmusic_client.get_album(yt, parsed["id"])
                ws_broadcast("progress", {"url": url, "status": "Downloading..."})
                downloader.download_album(album_info, yt_client=yt, progress_cb=progress_cb, force=True)

            elif parsed["type"] == "playlist":
                ws_broadcast("progress", {"url": url, "status": "Resolving playlist..."})
                browse_id = ytmusic_client.get_album_browse_id_from_playlist(yt, parsed["id"])
                if browse_id:
                    album_info = ytmusic_client.get_album(yt, browse_id)
                    ws_broadcast("progress", {"url": url, "status": "Downloading..."})
                    downloader.download_album(album_info, yt_client=yt, progress_cb=progress_cb, force=True)
                else:
                    ws_broadcast("progress", {"url": url, "status": "Could not resolve playlist"})
                    return

        with queue_lock:
            progress_dict[url] = "Completed"
        ws_broadcast("progress", {"url": url, "status": "Completed"})

    except Exception:
        logger.exception("Download failed for %s", url)
        with queue_lock:
            progress_dict[url] = "Error"
        ws_broadcast("progress", {"url": url, "status": "Error"})


@bp.route("/queue", methods=["POST"])
def add_to_queue():
    data = request.get_json()
    urls = data.get("urls", [])
    added = []
    with queue_lock:
        for url in urls:
            if url not in progress_dict:
                progress_dict[url] = "Queued"
                added.append(url)
                Thread(target=_download_url, args=(url,), daemon=True).start()
    return jsonify({"added": added}), 201


@bp.route("/progress/<path:url>", methods=["GET"])
def get_progress_for_url(url):
    from urllib.parse import unquote
    url = unquote(url)
    with queue_lock:
        status = progress_dict.get(url)
    if status is None:
        return jsonify({"error": "URL not found"}), 404
    return jsonify({"url": url, "status": status})


# --- Artist management API ---


@bp.route("/artists", methods=["GET"])
def list_artists():
    return jsonify(library.load_artists())


@bp.route("/artists/search", methods=["GET"])
def search_artists():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])
    yt = ytmusic_client.create_client()
    results = ytmusic_client.search_artist(yt, query)
    return jsonify(results)


@bp.route("/artists", methods=["POST"])
def add_artist_route():
    data = request.get_json()
    name = data.get("name", "").strip()
    channel_id = data.get("channelId", "").strip()
    if not name or not channel_id:
        return jsonify({"error": "name and channelId required"}), 400
    added = library.add_artist(name, channel_id)
    return jsonify({"added": added, "name": name, "channelId": channel_id}), 201 if added else 200


@bp.route("/artists/<channel_id>", methods=["DELETE"])
def remove_artist_route(channel_id):
    removed = library.remove_artist(channel_id)
    if not removed:
        return jsonify({"error": "Artist not found"}), 404
    return jsonify({"removed": True})


@bp.route("/check-now", methods=["POST"])
def trigger_check():
    """Manually trigger a release check."""
    Thread(target=scheduler.check_artists_for_new_releases, daemon=True).start()
    return jsonify({"status": "Check started"})


# Register blueprint with URL prefix
app.register_blueprint(bp, url_prefix=config.URL_PREFIX)


# --- Entrypoint ---

if __name__ == "__main__":
    logger.info("Starting YT Music → Jellyfin Bridge")

    # Ensure data directory exists
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.MUSIC_LIBRARY_DIR, exist_ok=True)

    # Rebuild ignore list from existing files
    library.rebuild_from_filesystem()

    # Start scheduler
    scheduler.start(run_immediately=True)

    # Start log tailer
    _ensure_tail_started()

    # Run web server
    app.run(host="0.0.0.0", port=config.WEB_PORT, debug=False)
