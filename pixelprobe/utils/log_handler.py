"""
Database log handler that stores log records in the LogEntry table.

Uses a background thread with a queue to avoid blocking application code
on database writes. Records are batch-inserted periodically.
"""

import logging
import queue
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

from pixelprobe.utils.log_context import current_scan_id, current_celery_task_id
from pixelprobe.constants import DEFAULT_LOG_EXCLUDE_LOGGERS, CONFIG_LOG_EXCLUDE_LOGGERS


class DatabaseLogHandler(logging.Handler):
    """Logging handler that writes log records to the LogEntry database table.

    Features:
    - Background thread with queue to avoid blocking app code
    - Batch inserts every 1s or when queue reaches 100 records
    - Silently drops records if queue is full (10k cap)
    - Reads exclude list from AppConfig (cached, refreshed in writer loop)
    - Separates traceback from message into dedicated field
    """

    QUEUE_MAX_SIZE = 10000
    BATCH_SIZE = 100
    FLUSH_INTERVAL = 1.0  # seconds
    FLUSH_ERROR_LOG_INTERVAL = 60.0  # seconds between repeated stderr messages

    def __init__(self, app):
        super().__init__()
        self.app = app
        self._queue = queue.Queue(maxsize=self.QUEUE_MAX_SIZE)
        self._exclude_loggers = {
            name.strip() for name in DEFAULT_LOG_EXCLUDE_LOGGERS.split(',') if name.strip()
        }
        self._running = True
        self._flush_error_logged_at = 0.0

        # Start background writer thread
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name='log-db-writer'
        )
        self._writer_thread.start()

    def emit(self, record):
        """Queue a log record for async database storage."""
        # Never store our own logs (prevent recursion)
        if record.name == 'pixelprobe.utils.log_handler':
            return

        # Check exclude list (reads only the cached set -- no DB access)
        if self._is_excluded(record.name):
            return

        try:
            # Build the record dict while we have context
            entry = {
                'scan_id': current_scan_id.get(None),
                'celery_task_id': current_celery_task_id.get(None),
                'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc),
                'level': record.levelname,
                'logger_name': record.name,
                'message': self.format(record) if self.formatter else record.getMessage(),
                'traceback': None
            }

            # Separate traceback into its own field
            if record.exc_info and record.exc_info[0] is not None:
                entry['traceback'] = ''.join(traceback.format_exception(*record.exc_info))

            self._queue.put_nowait(entry)
        except queue.Full:
            pass  # Silently drop -- stdout handler still has the record
        except Exception:
            pass  # Never let logging handler crash the app

    def _is_excluded(self, logger_name):
        """Check if a logger name matches the exclude list (cached, no DB access)."""
        for excluded in self._exclude_loggers:
            if logger_name == excluded or logger_name.startswith(excluded + '.'):
                return True
        return False

    def _refresh_exclude_cache(self):
        """Refresh the exclude list from AppConfig. Called from writer thread only (has app context)."""
        try:
            from pixelprobe.models import AppConfig
            config = AppConfig.query.filter_by(key=CONFIG_LOG_EXCLUDE_LOGGERS).first()
            if config and config.value:
                self._exclude_loggers = {
                    name.strip() for name in config.value.split(',') if name.strip()
                }
        except Exception:
            pass  # Keep existing cache on failure

    def _writer_loop(self):
        """Background thread that batch-inserts queued log records.

        Keeps a persistent Flask app context for the lifetime of the thread
        to avoid per-batch context creation overhead.
        """
        ctx = self.app.app_context()
        ctx.push()
        refresh_counter = 0

        try:
            while self._running:
                batch = []
                deadline = time.monotonic() + self.FLUSH_INTERVAL

                # Collect records until batch is full or timeout
                while len(batch) < self.BATCH_SIZE:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        entry = self._queue.get(timeout=remaining)
                        batch.append(entry)
                    except queue.Empty:
                        break

                if batch:
                    self._flush_batch(batch)

                # Refresh exclude cache every ~60 flush cycles (~60s at 1s interval)
                refresh_counter += 1
                if refresh_counter >= 60:
                    refresh_counter = 0
                    self._refresh_exclude_cache()

            # Drain remaining records on shutdown
            batch = []
            while not self._queue.empty():
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if batch:
                self._flush_batch(batch)
        finally:
            ctx.pop()

    def _flush_batch(self, batch):
        """Insert a batch of log entries into the database using bulk insert."""
        from pixelprobe.models import db, LogEntry
        try:
            db.session.bulk_insert_mappings(LogEntry, batch)
            db.session.commit()
            self._flush_error_logged_at = 0.0
        except Exception as e:
            # Rate-limit stderr reports to once per FLUSH_ERROR_LOG_INTERVAL so
            # recurring failures still surface without flooding logs.
            now = time.monotonic()
            if now - self._flush_error_logged_at > self.FLUSH_ERROR_LOG_INTERVAL:
                print(
                    f"[log_handler] Failed to flush {len(batch)} log entries to DB: {e}",
                    file=sys.stderr,
                )
                self._flush_error_logged_at = now
            try:
                db.session.rollback()
            except Exception:
                pass
            # Discard the scoped session so the next flush starts fresh. Protects
            # against rollback() silently failing on a broken connection, which
            # would otherwise leave db.session stuck in "rollback() fully before
            # proceeding" state for the life of the writer thread.
            try:
                db.session.remove()
            except Exception:
                pass

    def shutdown(self):
        """Stop the background writer thread gracefully."""
        self._running = False
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=5)
