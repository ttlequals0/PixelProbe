# Glossary

Every term PixelProbe uses, defined once and linked to the doc that covers it.

## Scanning

- **Scan** - A pass over one or more configured media directories that validates each file with FFmpeg, ImageMagick, or PIL and records a per-file result. See [How It Works](how-it-works.md).
- **Scan phase** - The lifecycle stage of a scan: `initializing`, `discovering`, `adding`, `scanning` (active) and `idle`, `completed`, `error`, `crashed`, `cancelled` (terminal). See [How It Works](how-it-works.md).
- **Discovery** - The directory walk that finds candidate files before any validation starts; phase 1 of a scan. See [How It Works](how-it-works.md).
- **Chunk** - A path-range slice of the pending file list, dispatched as one Celery task. Chunk size adapts to the number of pending files and is not configurable. See [Performance Tuning](performance-tuning.md).
- **Chunk heartbeat** - A thread inside each chunk task that bumps the scan's liveness timestamp every `CHUNK_HEARTBEAT_INTERVAL_SECS` so a scan busy on a long file is not mistaken for a dead one. See [How It Works](how-it-works.md).
- **Revival** - The stuck-scan sweeper's recovery path: when a scan's heartbeat is stale but chunk rows are still active (a container restart lost the queued tasks), claimed files are reclaimed and the chunks re-dispatched. See [Troubleshooting](troubleshooting.md).
- **Stuck-scan sweeper** - A scheduler job that runs every 5 minutes, crashes scans whose heartbeat is genuinely stale, and revives scans that lost their workers. See [Troubleshooting](troubleshooting.md).
- **Force rescan** - A scan that re-validates files even if they already have results, instead of only scanning new or pending files. See [Scan Types](scan-types.md).
- **Pending** - A file that is registered but has not been validated yet (or was reset for rescan). See [Scan Types](scan-types.md).
- **Orphaned scan** - A scan left active by a crashed or restarted worker; cleaned up at startup or revived by the sweeper. See [Troubleshooting](troubleshooting.md).

## Validation verdicts

- **Healthy** - The file decoded and validated without corruption signals. Benign decoder noise (NAL unit warnings, DTS/PTS timestamp warnings, ffmpeg 8 Opus EOF parse notices) does not affect this verdict.
- **Corrupted** - A corruption signal with a verdict fired: FFmpeg validation failure, JPEG pixel corruption, or a decode error flood. See [Scan Types](scan-types.md).
- **Warning** - A signal that is informative but does not prove damage: freeze events, frame-count mismatches, elevated TOUT or VREP, strict-decode notices, tool resource limits on oversized images. Warning files play back fine in most cases. See [Scan Types](scan-types.md).
- **Marked as good** - A manual override: the file keeps its scan history but is treated as healthy in stats and filters.
- **Error** - The file could not be read or scanned at all (permissions, I/O failure, unreadable media).

## Deep checks

- **Enhanced corruption analysis** - The staged deep check for video files: Stage 1 frame integrity, Stage 2 temporal outliers, Stage 3 multi-point sampling, Stage 4 strict error detection. See [How It Works](how-it-works.md).
- **Frame integrity check (Stage 1)** - Compares the counted packets (confirmed by a decode when they disagree by more than 5%) against the count expected from duration and frame rate. Warning-only: container metadata is unreliable on sparse-video and variable-frame-rate files.
- **Temporal outlier check (Stage 2)** - Samples three 10-second windows at 25/50/75% of the file and computes signalstats TOUT/VREP percentages. Warning-only.
- **TOUT (temporal outliers)** - A signalstats metric flagging pixels that differ from both temporal neighbors. Film grain triggers it on clean content, so it warns rather than condemns.
- **VREP (vertical line repetition)** - A signalstats metric from analog-tape QC; high values are normal in flat or graphic digital content, so it warns rather than condemns.
- **Multi-point sampling (Stage 3)** - Decodes short samples at several positions in very large files. Warning-only.
- **Strict error detection (Stage 4)** - A decode pass with aggressive error flags. Warning-only.
- **Freeze detection** - A full-decode pass with FFmpeg's `freezedetect` filter that reports stretches where the picture stops changing; black frames are filtered as false positives. Warning-only. Controlled by `FREEZE_DETECTION_ENABLED`. See [Configuration](configuration.md).

## Integrity and bitrot

- **Integrity check** - A re-hash of a previously scanned file to detect silent changes, scheduled through the rolling integrity queue. See [Scan Types](scan-types.md).
- **Rolling integrity queue** - The scheduler-driven queue that re-checks the oldest-checked files first under a time budget, so the whole library cycles through integrity checks continuously. See [How It Works](how-it-works.md).
- **Bitrot (suspected)** - A hash mismatch while the file's modification time is unchanged: the content changed without a legitimate write. See [Scan Types](scan-types.md).
- **Accept current state** - A manual action on a bitrot-suspected file that adopts the current hash as the new baseline.

## Configuration and operation

- **Exclusion** - A path or extension rule that removes files from scanning. See [Configuration](configuration.md).
- **Ignored error pattern** - An admin-defined FFmpeg stderr pattern that suppresses a corruption verdict for matching output. See [Configuration](configuration.md).
- **Scan schedule** - A cron or interval definition that launches scans automatically through APScheduler. See [Configuration](configuration.md).
- **Schedule time budget** - A cap on how long a scheduled integrity run may work per window. See [Configuration](configuration.md).
- **Notification provider** - A delivery channel for events: Pushover, ntfy, webhook, or email (SMTP). See [Configuration](configuration.md).
- **Notification rule** - A binding of one event type (scan started/completed/failed, corruption found, bitrot suspected, auth events) to one provider. See [Configuration](configuration.md).
- **Scheduler lock** - A Redis distributed lock so only one container runs the scheduler. See [How It Works](how-it-works.md).
- **Advisory lock** - The PostgreSQL lock that coordinates startup migrations across multiple workers. See [How It Works](how-it-works.md).

## Infrastructure

- **Celery worker** - The container that executes scan chunk tasks; concurrency is set by `CELERY_CONCURRENCY`. See [Performance Tuning](performance-tuning.md).
- **`MAX_WORKERS`** - ThreadPoolExecutor width for selected-file rescans inside a task; distinct from `CELERY_CONCURRENCY`. See [Performance Tuning](performance-tuning.md).
- **Portainer webhook** - The deployment trigger that recreates the stack with a new image version. See [Release Process](release-process.md).

[< Documentation index](README.md)
