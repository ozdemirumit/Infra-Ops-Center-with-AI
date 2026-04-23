"""
Log retention: delete old log files and rotated backups.
Runs daily (scheduled from Home.py via APScheduler).
"""

import time
from pathlib import Path
from logging_config.logger import get_logger

logger = get_logger("log_cleanup")

# Retention: delete backups older than this many days
DEFAULT_RETENTION_DAYS = 30


def cleanup_old_logs(logs_dir: Path = None, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """
    Delete log files older than retention_days.
    Keeps the active .log files; only removes rotated backups (.log.1, .log.2, ...)
    and plain log files that haven't been modified for N days.

    Returns number of files deleted.
    """
    if logs_dir is None:
        logs_dir = Path(__file__).resolve().parent.parent / "logs"

    if not logs_dir.exists():
        return 0

    cutoff = time.time() - retention_days * 86400
    deleted = 0
    errors = 0

    # Match *.log, *.log.1, *.log.2, etc.
    for logfile in logs_dir.glob("*.log*"):
        try:
            mtime = logfile.stat().st_mtime
            if mtime < cutoff:
                # Only delete rotated backups (have .N suffix), not active .log files
                if logfile.suffix != ".log" or logfile.name.endswith(tuple(f".log.{i}" for i in range(1, 10))):
                    logfile.unlink()
                    deleted += 1
        except OSError as e:
            logger.warning(f"Could not delete {logfile}: {e}")
            errors += 1

    if deleted:
        logger.info(f"Log cleanup: deleted {deleted} old logs (retention={retention_days}d, errors={errors})")
    return deleted


def start_cleanup_scheduler(interval_hours: int = 24):
    """
    Start a background thread that runs cleanup every N hours.
    Call once at app startup.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            func=cleanup_old_logs,
            trigger=IntervalTrigger(hours=interval_hours),
            id="log_cleanup",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(f"Log cleanup scheduler started (every {interval_hours}h)")
        return scheduler
    except ImportError:
        logger.warning("APScheduler not available — log cleanup disabled")
        return None
