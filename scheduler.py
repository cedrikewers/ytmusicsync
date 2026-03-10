import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import config
import library
import downloader
import ytmusic_client

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def check_artists_for_new_releases() -> None:
    """Main periodic job: check all followed artists for new releases."""
    artists = library.load_artists()
    if not artists:
        logger.info("No artists configured. Add artists via the web interface.")
        return

    yt = ytmusic_client.create_client()
    current_year = datetime.now().year
    min_year = current_year - 1  # Coarse year filter before precise date check
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.LOOKBACK_DAYS)

    logger.info("Checking %d artists for new releases (last %d days)...", len(artists), config.LOOKBACK_DAYS)
    total_downloaded = 0

    for artist in artists:
        name = artist.get("name", "Unknown")
        channel_id = artist.get("channelId", "")
        if not channel_id:
            continue

        try:
            releases = ytmusic_client.get_artist_releases(yt, channel_id)
        except Exception:
            logger.exception("Failed to get releases for %s", name)
            continue

        for release in releases:
            year_str = release.get("year", "")
            try:
                year = int(year_str)
            except (ValueError, TypeError):
                year = current_year

            # Coarse filter: skip anything obviously too old
            if year < min_year:
                continue

            browse_id = release.get("browseId", "")
            if not browse_id:
                continue

            # Quick check: is this already downloaded?
            release_title = release.get("title", "")
            dir_name = downloader.make_directory_name(release_title, name)
            if library.is_downloaded(dir_name):
                continue

            # Precise date check: fetch album, get first track's publish date
            try:
                album_info = ytmusic_client.get_album(yt, browse_id)
            except Exception:
                logger.exception("Failed to fetch album %s by %s", release_title, name)
                continue

            first_video_id = None
            for t in album_info.get("tracks", []):
                if t.get("videoId"):
                    first_video_id = t["videoId"]
                    break

            if first_video_id:
                release_date = ytmusic_client.get_release_date(yt, first_video_id)
                if release_date is not None and release_date < cutoff:
                    logger.debug(
                        "Skipping %s by %s — released %s, before cutoff %s",
                        release_title, name, release_date.date(), cutoff.date(),
                    )
                    continue

            # Download
            try:
                downloaded = downloader.download_album(album_info, yt_client=yt)
                if downloaded:
                    total_downloaded += 1
            except Exception:
                logger.exception("Failed to download %s by %s", release_title, name)

    if total_downloaded:
        logger.info("Downloaded %d new releases", total_downloaded)
    else:
        logger.info("No new releases found.")


def start(run_immediately: bool = True) -> BackgroundScheduler:
    """Start the background scheduler.

    If run_immediately=True, runs the check once before scheduling.
    """
    global _scheduler

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        check_artists_for_new_releases,
        "interval",
        hours=config.CHECK_INTERVAL_HOURS,
        id="check_new_releases",
        replace_existing=True,
    )
    _scheduler.start()

    if run_immediately:
        logger.info("Running initial release check...")
        try:
            check_artists_for_new_releases()
        except Exception:
            logger.exception("Initial release check failed")

    logger.info(
        "Scheduler started. Checking every %d hours.", config.CHECK_INTERVAL_HOURS
    )
    return _scheduler


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
