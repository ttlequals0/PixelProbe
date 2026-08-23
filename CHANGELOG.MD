# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0).

## [2.8.7] - 2026-08-23

### Added

- **Tunables: scanner settings you can edit while a scan runs.** Detection thresholds, per-file work limits and storage timeouts were environment variables read once at import, so changing one meant editing a compose file and restarting. They are stored in the database now, editable under **System > Tunables** or through `GET`, `PUT` and `DELETE` on `/api/settings`, and a change reaches a running scan without a restart. Fifteen settings across three groups: Detection decides what gets flagged, Performance bounds what one file may cost, Timeouts bound how long to wait on storage. One registry defines each setting's type, range, default and wording, and the API validation, the settings screen and the documentation table all derive from it.
- **A settings screen that shows what you changed.** Each row carries its label, a sentence explaining what the value does, the current value and a reset. Anything differing from the shipped default is marked, because on a settings screen the useful question is which values are no longer stock.

### Changed

- **The shortest reported freeze is now 7 seconds, up from 5.** Animation holds a drawing still for several seconds at a time, and at 5 seconds ordinary cartoons were reported as freezes. The value drives `freezedetect` directly, so shorter events are never produced and never cost a confirmation pass. Measured on a 21k-file library, the freeze warning rate had already fallen from 7.8% to 2.8% with the v2.8.6 filters; the remaining reports were dominated by held animation cels.
- **Scanner settings no longer read the environment.** On the first start after upgrading, any of the old variables still set in your environment is copied into the database once, so behaviour does not change under an operator who never opens the settings screen. After that the stored value is authoritative and the variable is ignored.
- Freeze detection can be switched off from the settings screen rather than only through `FREEZE_DETECTION_ENABLED`.

### Fixed

- **The settings API no longer reflects request data into its responses** (CodeQL `py/reflective-xss` and `py/stack-trace-exposure`). A rejected key was echoed back in the error, and a rejected value's message was built by the validator that had just handled the raw input. Errors are now composed from the registry alone: an unknown key gets a fixed message pointing at `GET /api/settings`, and an out-of-range value gets the setting's name and its accepted range, which is more use than repeating what was typed.
- **The number column in the settings screen lines up.** Rows without a unit pushed their input right, so the values zig-zagged down the panel. The control area is a fixed three-slot grid now: value, unit, reset.

### Tests

- 31 new tests: registry consistency, value coercion and range errors for every type, resolution precedence, an out-of-range stored value falling back to its default, an unreadable database yielding defaults rather than failing a scan, the API's grouped read, whole-batch rejection leaving nothing written, reset, authentication, proof that a changed setting reaches the `freezedetect` command, and a check that a script tag sent as a setting key is never echoed back.
- The freeze toggle test now exercises the setting rather than the environment variable. It previously passed for the wrong reason: its event sat inside the title card window, so no freeze was reported either way.

## [2.8.6] - 2026-08-22

### Added

- **Data integrity check for every media file, ahead of any decode.** An interrupted download or copy leaves a file at its correct length with regions inside it that were never written. Demuxers skip past those regions, so the picture holds while the clock keeps running, and freeze detection reported that as a frozen picture rather than as missing data. Allocated blocks against nominal size is only a gate deciding which files are worth opening, since compression and dedup under-allocate healthy files too; the verdict comes from `SEEK_HOLE`, which reports regions the filesystem never allocated and reads no file data. That distinction is the whole point: a valid file can legitimately contain long runs of zero bytes, such as digital silence in PCM audio, and those bytes were written. A file with unwritten regions is marked **corrupted** and skips decoding entirely, which also removes a full-length decode pass from every damaged file. A filesystem without sparse-region support yields no verdict rather than a guess. Tunable via `DATA_HOLE_ALLOC_RATIO` and `DATA_HOLE_MIN_PCT`.
- **Shared `env_float` helper** in `pixelprobe/utils/helpers.py`, alongside the existing `env_int`, with optional floor and ceiling clamping.

### Fixed

- **Static title and end cards are no longer reported as freezes.** Programmes open and close on motionless plates: distributor logos, copyright notices, sponsor credits. The picture genuinely stops, so the detector was right and only the verdict was wrong. A solitary freeze no longer than `STATIC_CARD_MAX_SECS` sitting within `STATIC_CARD_EDGE_SECS` of either end of the file is now discounted, with the reason recorded in the scan transcript. The edge window is also capped at `STATIC_CARD_EDGE_FRACTION` of runtime so it cannot reach the middle of a short clip and swallow a genuine freeze.
- **Held animation cels are no longer reported as freezes.** `freezedetect`'s noise tolerance compares a whole-frame mean. Limited animation holds its background and moves only small figures, which shifts that mean by a fraction of the threshold, so the segment reported as frozen even though every frame in it differed. Surviving candidates are now re-checked over their own window at a tolerance only repeated frames can clear. Each check is its own ffmpeg process, bounded both by `FREEZE_CONFIRM_MAX_WINDOWS` (default 20) and by `FREEZE_CONFIRM_BUDGET_SECS` (default 600) of wall time per file, so a file reporting hundreds of events cannot stall a scan. A confirmation pass that times out, exits non-zero, hits either bound, or cannot run keeps the candidate, so a failure never erases a finding.
- **Healthy files no longer print their scan transcript in the results table.** With no corruption, warning, or error to report, the Details column and the mobile card fell through to the raw ffmpeg output, so every healthy file carried a multi-line transcript in the cell and in its hover tooltip. Those cells report what the scan concluded, and a healthy file concluded nothing worth a line; the transcript stays one click away under View. The details modal also stopped auto-expanding the transcript for exactly those files, which was the one case where it opened uninvited.
- **Reported frozen time can no longer exceed the runtime.** Freeze durations were summed naively, so events overlapping the same span were counted once per event and produced percentages above 100%. Overlapping events are now merged before summing and the percentage is capped at 100%.

### Security

- **Reflected request data removed from the deprecated `/api/scan-parallel` response** (CodeQL `py/reflective-xss`, alert 87). The endpoint echoed `force_rescan` straight from the request body into its JSON response. The input decorator already rejects a non-bool, so the flow was not exploitable, but the value is now coerced before it is echoed. Pre-existing on main, unrelated to the freeze work in this release.

### Changed

- Freeze detection parsing moved into a shared `_parse_freeze_events` helper, used by both the detection pass and the confirmation pass. `get_file_info` now also returns the file's allocated block count, so the data integrity check needs no stat of its own.
- Documented the full set of failure types in `docs/scan-types.md`, covering every verdict, the reasons behind each, and the findings PixelProbe deliberately discounts along with why.

### Tests

- 22 new tests covering static card suppression at both ends of a file, the relative edge bound on short clips, multi-event files never being treated as cards, confirmation pass accept and reject paths, confirmation timeout and missing-binary fallbacks, overlapping event merging, and the data integrity check across holed files, compressed filesystems, filesystems that do not report block counts, undersized files, and unreadable paths.
- The `_check_video_corruption` propagation test moved its freeze event into the body of the file; the previous fixture placed a 5-second event 5 seconds into a 60-second file, which is a title-card shape under the new rule.

## [2.8.5] - 2026-08-19

### Fixed

- **Valid oversized images are no longer reported corrupted.** Any image past Pillow's decompression-bomb threshold (~358MP at defaults) fell through to ImageMagick, whose `cache resources exhausted` exit landed in the generic corruption branch, so a valid 625MP scan was flagged the same as real pixel damage. Both guards are resource limits, not corruption evidence; they now produce a warning verdict following the existing HEIC libheif pattern.
- **Parallel file-list scans no longer share one database connection across worker threads.** The scan-files API path (default `num_workers=4`) fanned a single PixelProbe instance across a thread pool while its engine used StaticPool, one raw psycopg2 connection that psycopg2 forbids using concurrently; the symptom was sporadic save and cache failures. The worker engine now uses a QueuePool sized to the worker count, and a postgres-backed regression test fails against the old pool.

### Changed

- **Frame integrity check: packets first, decode only to confirm, warning verdict.** Stage 1 paid a full sequential `-count_frames` decode on every video and then discarded the result - ffprobe's csv column order never matched what the parser expected under ffmpeg 8, so the check has been silently inert while costing minutes on large files. It now compares the near-free packet count first and runs the full decode only on a mismatch. A confirmed mismatch produces a warning, not a corruption verdict: container framerate metadata lies on sparse-video files (a 240s QuickTime fixture with 244 real frames declares 25fps), and real decode damage is caught by the deep-decode stage.

### Removed

- Dead `ScanExecutor`/`BatchProcessor` (`pixelprobe/services/scan_executor.py`), superseded by the Celery task path; its only callers were its own tests.

### Tests

- **Fixture corpus repaired.** Four committed "valid" samples (3gp, flv, mpg, wmv) were 189-byte HTML error pages from failed downloads; valid.mkv failed h264 decode under ffmpeg 8; valid.webp failed both PIL and ImageMagick; six "corrupted" samples (mp3, aiff, jpg, png, gif, bmp) decode cleanly under modern tools because the FFmpeg-bug-tracker bugs they exercised were in FFmpeg, not the files. All are replaced by a committed deterministic generator script, with per-format detection expectations documented (mpg and gif are warning-level by design).
- **real_media suite made runnable and wired into CI.** The scan fixture rescanned every sample per test and blew each test's timeout, so the suite could never complete - and CI always deselected the marker, which is how the fixture rot went unnoticed. Scans are now session-cached, the tautological `scan_status == 'completed'` assertion clause is gone, and CI runs the suite on the Python 3.12 leg with a PostgreSQL service for the concurrency test.
- New synthetic corruption matrix (truncation at header/mid/tail, zero-byte files, PNG-renamed-to-JPG, scattered mdat damage, SVG/PSD/progressive-JPEG/animated-WebP, symlink-loop discovery) plus oversized-image regression tests.

## [2.8.4] - 2026-08-18

### Fixed

- **Scan progress no longer freezes after a revival.** Each revived chunk task restarted its tally at zero and overwrote the chunk's cumulative `files_scanned`, so the scan-wide sum collapsed below the stored high-water mark and the progress counter froze there (observed in production: headline stuck at 3,386 while chunk sums restarted from 131), with the final report undercounting by every pre-revival file. Chunk accounting is now additive across attempts, and the base is cross-checked against files actually finished in the chunk's range since the scan started, which also repairs tallies already clobbered by earlier revivals - the live scan self-heals on its next revival cycle.

## [2.8.3] - 2026-08-18

### Fixed

- **VREP (vertical line repetition) no longer sets warning status.** 298 production files carried the "Elevated vertical line repetition" warning; spot checks across the full 10-96% severity range were all visually pristine, with dark scenes, letterboxing, and monochrome lighting as the common factor. Combined with the 2.8.0 measurement that genuinely corrupted content scores lower than clean synthetic content on this metric, the warning has no discriminating power on born-digital video (signalstats' VREP exists to catch analog tape head-clog line repeats). The percentage is still recorded as a note in the scan output; TOUT keeps its warning.

## [2.8.2] - 2026-08-18

### Fixed

- **Results table and cards rendered nothing in 2.8.1.** The details-priority refactor left both render functions with `const details = details;`, a self-referential declaration that throws at first render, so the dashboard showed no items. The line now calls `fileDetails(file)` as intended. 2.8.1 was live for under an hour; no data was affected.

## [2.8.1] - 2026-08-18

### Fixed

- **Opus "corruption" false positives (81 of 84 corrupted files were this).** ffmpeg 8 emits a single `Error parsing Opus packet header` for the final packet at EOF on MKV files that ffmpeg 6 validates silently - reproduced on the flagged files, where a full audio decode also completes cleanly with exit code 0. The validation stderr classifier treated any line containing "error" as a corruption verdict even when ffmpeg exited 0, so every Opus-in-MKV file hit by the artifact was condemned. Opus packet-header parse notices with exit code 0 now join the NAL/DTS/reference-frame benign group and the file stays healthy. The benign path is bounded (`OPUS_EOF_NOTICE_MAX`, default 4, roughly one notice per Opus stream): a flood of parse errors means mid-stream damage that a `-c copy` validation cannot surface through the exit code, because audio is never decoded, and still condemns the file.
- **High-TOUT verdict demoted from corruption to warning.** The one non-Opus video flagged this scan was condemned at "17% temporal outliers"; re-sampling the same windows put 46-100% of frames over the per-frame TOUT threshold while extracted frames were visually pristine - film grain drives the metric, exactly the failure mode that demoted VREP in 2.8.0. TOUT now warns instead of condemning. Decode errors, frame integrity, and freeze detection keep their existing verdicts.
- **Freeze events no longer display a neighbor's end timestamp.** ffmpeg emits `freeze_start`, then `freeze_duration`, then `freeze_end`, but the parser closed each event on `freeze_duration`, so the following `freeze_end` landed in the next event's dict - producing impossible ranges like "5955.3s - 5691.4s (duration: 5.3s)". Events now close only when all three fields are present, and a new `freeze_start` over a partial event resyncs the parser so a single unparsed line cannot shift every later event. Display-only: the black-frame filter and totals always used start + duration, so no verdicts changed.
- **Stage 2 operational noise no longer sets warning status.** "Temporal outlier check sampled only N frame(s)" and "partially timed out" describe a run of the check aborted by load (both sample windows exceeding the 30s timeout while ~19 concurrent HEVC decodes saturate the CPU), not the file - yet they accounted for 43 of the first 100 warnings in production. These outcomes are now recorded in the scan output (as `Result: INCOMPLETE` when no verdict was reached, so an aborted check can never read as `PASSED`) and the file's warning status is left to signals that actually measured it.
- **Details views now surface the verdict before the raw tool output.** The results modal ordered sections corruption/scan output/error/warning, and the row and card fallbacks picked scan output over warning details, so a warning file's most useful line was buried under the full FFmpeg transcript. One shared priority (corruption, warning, error, scan output) now drives the table cells, mobile cards, and the modal, and the modal was redesigned around it: verdict sections render as severity-tinted cards up top and the raw transcript collapses behind a "Full scan transcript" disclosure (expanded only when it is the sole content). The modal is titled "Scan Details".

### Documentation

- **Docs restructured to a flat topic layout with a glossary and a full accuracy audit.** `docs/` is now flat lowercase topic files with an annotated index; ARCHITECTURE and SYSTEM_ARCHITECTURE merged into `how-it-works.md`; new `glossary.md` links every app term to its doc; new `web-interface.md` carries the UI walkthrough and screenshots; the root README is a short overview with a quick start and a table linking every doc. Every doc was audited against the code: the database schema doc was rewritten for all 17 models, the API reference corrected against the real routes, stuck-scan revival documented, stale commands fixed, and issue #78's performance-tuning errors corrected. Release tagging is scripted (`scripts/publish_release.sh`) with notes extracted from this changelog.

### Security

- **Scan-launch responses no longer echo caller-supplied directory paths (CodeQL py/reflective-xss, both open alerts).** `/api/scan-files-parallel` and `/api/scan-parallel` returned the validated request paths in the response body; the frontend never read the field. The field is simply removed; the paths remain in the server-side audit log where the 2.8.0 pass already put them.

## [2.8.0] - 2026-07-24

### Added

- **Email (SMTP) notification provider (#69).** Email joins pushover, ntfy and webhooks as a `provider_type`, so it reuses the existing notification rules rather than introducing a parallel delivery path: point a rule at an email provider and the events already dispatched (`scan_start`, `scan_complete`, `scan_failed`, `scan_missed`, `corruption_found`, `bitrot_suspected`, the user/API-key/auth events) arrive as mail. Configuration lives in the provider's existing JSON column, so no database migration is needed: `smtp_host`, `smtp_port` (defaults to 465 for SSL, otherwise 587), `security` (`starttls` by default, or `ssl`/`none`), optional `username`/`password`, `from_address`, and `recipients` as either a list or a comma-separated string. STARTTLS and implicit SSL both verify certificates against the system trust store. One connection per send under a hard timeout, and a send failure is logged and returned rather than raised, because dispatch runs inside scan paths and a broken mail server must not fail a scan. The saved password is masked as `***` by the API like other provider secrets, and `POST /api/notifications/providers/<id>/test` sends a test message.
- Emails carry `Date` and `Message-ID` headers. `Date` is mandatory per RFC 5322 and `smtplib` does not add one, so without it spam filters penalize the message and strict MTAs can reject it. `Message-ID` is derived from the sender's domain rather than the library default, which would otherwise leak the container hostname.
- SMTP hosts are validated with a new `validate_outbound_host()` that deliberately permits private, LAN and loopback targets, because a self-hosted relay usually is on localhost, a Docker network or the LAN, while still refusing cloud-metadata and link-local addresses so the looser policy cannot become a credential-theft path. Webhook and ntfy URLs keep the stricter `validate_safe_url()` policy. The host is re-checked immediately before connecting, not only when saved, so a DNS rebind cannot repoint a stored hostname.

### Security

- **Pillow 12.2.0 to 12.3.0**, resolving 13 CVEs (CVE-2026-59197 through 59205, 55379, 55380, 55798, 54058 through 54060).
- **pillow-heif 0.22.0 to 1.3.0**, resolving CVE-2026-28231. The major version bump is API-compatible here: the only entry point used is `register_heif_opener()`, and 1.3.0 requires `pillow>=11.1.0`.
- **Reflected caller input removed from error responses (CodeQL py/reflective-xss).** Three endpoints echoed a caller-supplied path or name straight back into the error body; they now return a fixed message and log the rejected value server-side, where it is actually useful for debugging. Two further endpoints had the identical pattern without being flagged yet and were fixed in the same pass, so the next scan does not simply surface them instead.
- **Local-only operator notes are excluded from the Docker build context.** The Dockerfile copies the whole working directory, so untracked local files such as `CLAUDE.md`, `AGENTS.md`, `GEMINI.md` and `.claude/` were being baked into the image even though git never saw them. Being gitignored protects the repository, not the image. `.dockerignore` now excludes them, along with `.env*` as a glob (previously only exact `.env` plus two named files) and the missing `.pytest_cache`, `dist`, `build` and `coverage*` entries.
- **Webhook URLs no longer reach the logs (CodeQL py/clear-text-logging).** A Slack or Discord webhook URL is itself a bearer credential - anyone holding it can post as you - and ntfy URLs carry the topic, yet the full URL was written in clear text on every successful webhook send and again whenever the SSRF check blocked or allowed an internal target. Those three sites now record the hostname and resolved IP only, which is what a blocked-target diagnosis actually needs.
- **A masked secret sent back unchanged no longer overwrites the real credential.** Because the API returns secrets as `***`, a client that fetched a provider, edited one field and submitted the whole object would store the literal mask and silently break delivery. Masked values are now resolved back to the stored secret for every provider type.

### Fixed

- **Healthy videos no longer flagged corrupted for "excessive vertical line repetition" (regression from #68).** Stage 2 of the enhanced check read frames through the `movie=` lavfi source, and that source silently stops after the first few seconds and exits 0 - so the statistics only ever described the opening moments of a file. Measured on the reported files: a 41,608-frame video yielded 19 frames (0.76s), and two features yielded 262 and 131 frames (11.1s and 5.5s). Intros are fades, black frames and title cards, which is exactly where vertical-line repetition legitimately approaches 1.0, so any video over 1GB could be condemned on its own title card - one file was reported "corrupted" at 47.4% from those 19 frames while all 41,608 frames decode cleanly. Stage 2 now samples three 10s windows at 25/50/75% of duration (`TEMPORAL_SAMPLE_SECS`, `TEMPORAL_SAMPLE_TIMEOUT_SECS`), deliberately skipping both the intro and the credits, and refuses to issue a verdict at all below `TEMPORAL_MIN_FRAMES` (100) sampled frames rather than extrapolating from a handful. The same files now sample ~720 frames each and come back clean. Note this signal had been dead code for as long as ffprobe lacked the `pkt_pts_time` field it asked for; #68 restored the field name, which re-enabled a stage that had never actually run.
- **Vertical line repetition is now a warning rather than a corruption verdict.** Tested against real damage, the metric does not discriminate: clean synthetic content scored VREP>0.5 on 45.5% of frames while a file with genuine byte corruption and decoder concealment scored 32.9%, and even extreme 10x row duplication peaked at 0.833 - so no per-frame threshold separates the two. signalstats is analog-tape QC tooling (QCTools heritage) where high VREP is expected in born-digital video; flat or graphic content such as animation, screen recordings and letterboxed dark films scores high while perfectly healthy. It is still reported, now through the warnings path alongside Stages 3 and 4, which are warnings-only for the same oversensitivity reasons. TOUT (temporal outliers) keeps its corruption verdict.
- **A slow sample window no longer discards the windows that did read.** Each window has its own timeout and contributes independently, and a partial timeout is surfaced as a warning instead of silently reducing the sample the verdict rests on. Two timeouts abandon the remaining windows, since whatever stalled them (dead mount, failing sectors, storage slower than realtime decode) applies to the rest, rather than blocking a worker for the full 90s to reach the same warning. The old code also appended a timeout string to `corruption_details`, which the caller only reads when the file is already corrupt, so that message could never reach anyone.
- Moving off `movie=` also removes a latent quoting bug: file paths were interpolated into a lavfi filtergraph with no escaping, so any path containing `:`, `,`, `'`, `\`, `[` or `]` produced a malformed graph rather than a scan result. The path is now passed as an ordinary argv element via `ensure_cli_safe_path()`.

### Changed

- The provider test endpoint now calls `NotificationService.test_provider()` instead of duplicating its own test title and message, so the two no longer drift and the service method is no longer dead code. The API validator and the SMTP sender likewise share one `resolve_smtp_port()` helper, so they cannot disagree about which port a provider will use.
- Stage 2 and Stage 3 now share one `_probe_video_duration()` helper (stream duration preferred, container duration as fallback) instead of duplicating the same fallback chain, and both accept a duration from the caller. `_check_video_corruption` already probes duration and passes it to freeze detection, so the sampling stages now reuse that value instead of spawning one or two more full `ffprobe` calls per file - on a library of over a million files those add up to a large number of avoidable process spawns per scan.
- The signalstats verdict thresholds are now named constants (`TEMPORAL_TOUT_FRAME`, `TEMPORAL_TOUT_PERCENT`, `TEMPORAL_VREP_FRAME`, `TEMPORAL_VREP_PERCENT`) carrying the measurements behind them, rather than inline numbers whose rationale lived only in this changelog.

## [2.7.3] - 2026-07-17

### Fixed

- **Scans busy on long movies are no longer falsely marked crashed (#75).** The stuck-scan check crashes any scan with no progress update for 30 minutes, but progress was only written when a file finished - and a single large movie can legitimately spend 30-60+ minutes inside ffmpeg validation, so a run of big files tripped the rule while the workers were healthy. Each chunk task now runs a heartbeat thread that bumps the scan's liveness timestamp every `CHUNK_HEARTBEAT_INTERVAL_SECS` (default 120s), so `last_update` staleness now genuinely means the workers are dead. Note the semantics change: `last_update` is a liveness heartbeat now, not a last-file-completed timestamp.
- **Scans interrupted by container restarts now resume instead of dying (#75).** Restarting the stack mid-scan could lose the queued chunk tasks; startup kept the scan alive ("within grace") but nothing ever re-queued its chunks, so it sat idle until the 30-minute rule crashed it. The stuck-scan sweeper now detects this state - stale heartbeat with chunk rows still active - reclaims the dead workers' claimed files, and re-dispatches the chunks (up to 3 revival attempts, staleness threshold `CHUNK_REVIVE_STALENESS_SECS`, default 600s). Duplicate-delivery guards make revival safe against ghost redeliveries: a task whose chunk is already finished or owned by a newer task id exits without touching anything, which also fixes a latent bug where a redelivered message could reset a completed chunk and zero its file count. Chunk task ids are now assigned before dispatch (previously written after, from the group result), and the discovery walk heartbeats too, so a slow sparse-tree walk cannot be falsely crashed either.
- **Worker crash mid-chunk no longer silently drops files.** With late acks, a chunk whose worker died mid-scan is redelivered under the same task id - but its files were already claimed, so the redelivery found nothing pending and marked the chunk complete with zero files, dropping the in-flight files from the scan. The redelivery now reclaims the dead attempt's files and scans them.
- **Cancel button invisible in light mode (#74).** The button referenced a CSS variable that was never defined, so its background resolved to transparent - white text on a white page. Same fix applied to the pagination hover background, which had the identical defect.
- **"Files by type" chart unreadable in light mode (#74).** Axis labels, title, and grid lines were hardcoded to dark-theme colors. They now read the active theme's CSS variables at render time, and the file-type labels got a bump from 10px to 11px in both themes.
- **Integrity check started during a running scan no longer strands the UI (#74).** The progress panel can only track one operation at a time; when an integrity check finished, it stopped all polling and left the scan buttons disabled with no way back. On completion the UI now checks whether a scan is still running and returns to its progress view, or re-enables the scan buttons if not. Switching operations also no longer leaks a duplicate poll loop.

## [2.7.2] - 2026-07-16

### Added

- **Pending Only filter button (#72).** The dashboard showed a pending-files count but offered no way to list them; the results API already accepted `scan_status=pending`, so the button just exposes it. Useful for seeing exactly which files a scan has not gotten to - or, after an incident like #70, which files never completed.
- **Bitrot Suspected stat card (#72).** The count was already in the stats payload (and buried in the integrity card's tooltip) but had no card of its own despite having a filter button.

### Changed

- **Dashboard stat cards reordered to mirror the filter buttons (#72):** total, corrupted, warnings, bitrot, healthy, pending, with integrity coverage last. The cards remain read-only stats; they are deliberately not clickable filters.

## [2.7.1] - 2026-07-16

### Fixed

- **A single unreadable file no longer hangs an entire scan (#70).** External tools (ffprobe, ffmpeg, ImageMagick) already run under hard timeouts, but the pure-Python reads that precede them - os.stat, libmagic type detection, and the SHA-256 hash read - had none. A file on stalled storage (dead NFS/SMB/fuse mount, failing disk sector) blocked the chunk worker in the kernel forever with no ffmpeg or ImageMagick process visible, the progress counter and ETA froze, and because the file was still `pending`, restarting the containers resumed the scan straight back into the same hang. These reads now run under a watchdog deadline (`FILE_READ_TIMEOUT_SECS`, default 60s; the hash deadline additionally scales with file size at an assumed 5MB/s floor). On timeout the file is marked corrupted with a "stalled read" detail, the result is persisted so no scan path re-selects the file, and the scan moves on. A stalled read that times out abandons one watchdog thread until the kernel releases it; at 32 concurrently stalled reads new reads fail fast instead of piling up, so a fully dead mount degrades to fast per-file errors rather than fd exhaustion.

## [2.7.0] - 2026-07-16

### Breaking

- **docker-compose.yml now defaults to PostgreSQL 18 (#67).** PostgreSQL data directories are not portable across major versions: an existing `postgres_data` volume created by PostgreSQL 15 will not start on the 18 image. Follow the new migration guide in docs/DOCKER_SETUP.md (dump on 15, restore into 18, refresh collation) BEFORE pulling the new compose file, or pin `postgres:15-alpine` to stay. The application itself works with PostgreSQL 15 through 18, so only the compose default is breaking - existing deployments that manage their own database are unaffected until they choose to migrate.

### Changed

- **Base image upgraded to Ubuntu 26.04 (#67).** FFmpeg 6.1.1 becomes 8.0.1 (faster demuxing/probing) and ImageMagick 6.9 becomes 7.1.2. The app pins Python 3.12 via deadsnakes and runs from a dedicated virtualenv (Ubuntu 26.04 ships 3.14, which psycopg2-binary has no wheels for yet). The image checker now invokes `magick` when available, falling back to `convert` on ImageMagick 6 systems, and the temporal-outlier probe uses `pts_time` (its `pkt_pts_time` field was removed in newer FFmpeg, which silently disabled that signal). Kernel header packages are purged from the final image, clearing the long tail of unfixable linux-libc-dev CVEs from scans.
- **Task broker image switched from Redis 7 to Valkey 9 (#67).** Drop-in replacement: the compose service name, all redis:// URLs, and the wire protocol are unchanged, and the Valkey image ships redis-* compatibility symlinks. Valkey stays open source (BSD) while Redis moved away from it after 7.2.

### Fixed

- **Closing the media viewer now actually stops playback.** The viewer modal was only hidden on close, so a playing video or audio file kept playing (and buffering) in the background. Closing now pauses the player and detaches its sources.
- **Documentation accuracy audit.** All docs were checked against the codebase and corrected: removed endpoints that never existed (`/api/scan-all`, `/api/stats/summary`, `/api/scan/parallel-v2*`, `/api/export/csv`, `/api/cleanup`), fixed the example compose file (broken celery command, wrong healthcheck path, missing SECRET_KEY, fictional CELERY_WORKERS/REDIS_HOST vars), replaced the nonexistent Celery beat with the real APScheduler jobs, corrected pool sizes, session lifetime, task lists, chunking behavior, and rewrote PERFORMANCE_TUNING.md around env vars that actually exist. Example API clients now authenticate and call real endpoints. openapi.yaml: removed a duplicate path key and a nonexistent DELETE route, completed the schedule scan_type enum.
- **Benign ImageMagick warning classification never matched.** The gates that classify PNG chunk and profile warnings as non-corruption checked for `@warning/png.c`, but ImageMagick (6 and 7 alike) emits `@ warning/png.c` with a space - so the benign-warning paths were unreachable and such files fell through to the generic error-keyword check. Verified against real ImageMagick output on both versions.

### Added

- **CI now builds the Docker image and runs the real-media parser tests inside it.** The FFmpeg/ImageMagick stderr parsers fail soft (a format change yields zero detections rather than an error), so unit tests on the runner's own tool versions cannot catch a base-image regression. The new job exercises corruption detection against the exact ffmpeg 8 / ImageMagick 7 binaries that ship in the image.
- **PostgreSQL 15 to 18 migration guide** in docs/DOCKER_SETUP.md, including volume backup, restore verification, and collation refresh.

## [2.6.65] - 2026-07-15

### Fixed

- **Active scans no longer get falsely marked crashed (#65).** Two defects compounded. First, the app writes timezone-aware UTC datetimes into naive TIMESTAMP columns and reads them back assuming UTC, but PostgreSQL converts aware values to the session timezone before dropping the offset - on a server with a non-UTC default timezone every scan timestamp was stored as local wall time, making a running scan look hours stale and tripping the stuck-scan checks within seconds of starting. The database session timezone is now pinned to UTC at connect time. Second, the stuck-scan checks treated the orchestrator task's SUCCESS state as "task lost" - but the orchestrator finishes as soon as chunk tasks are queued, so a chunked scan was declared dead while its chunks were actively scanning. Both the scheduler sweep's task-gone branch and the scan-launch check now consider a scan alive while any of its chunks are pending or processing and the scan has updated within the last 30 minutes; the 30-minute no-progress hard timeout stays as the backstop so a worker that died mid-chunk cannot block scans forever. The same fix also revived the sweep's Celery task check, which had been silently disabled since it was added: it imported `safe_check_task_state` from a module that never contained it and swallowed the ImportError. Symptom: progress bar disappears and the scan button resets mid-scan while file counts keep climbing, with `/api/scan-status` reporting `phase=crashed`.
- **Scheduler lock contention between web and Celery containers (#64).** Every process importing the app raced for the Redis scheduler lock, and lock identity was hostname:pid - unsafe in podman pods, where containers share a hostname while pids collide across pid namespaces. A collision let one container force-steal a live lock ("self-lock"), and the heartbeat then overwrote the key unconditionally, so two schedulers could run at once. The lock value now carries a per-process uuid, acquisition is strictly SET NX (dead holders recover via the 60s TTL, never by force-steal), and the heartbeat refreshes the TTL with an atomic compare-and-expire that cannot clobber another holder. The standby retry loop also retries indefinitely instead of giving up after 5 minutes, so failover keeps working long after boot.

### Added

- **`SCHEDULER_ENABLED` environment variable (#64).** Set `false` on a container to keep it out of scheduler lock arbitration entirely (default `true`). docker-compose.yml now sets it to `false` on the web service so only the Celery worker runs the scheduler, matching the documented intent.
- **`GUNICORN_BIND` environment variable for IPv6 / dual-stack deployments (#63).** Gunicorn settings moved from the hardcoded Dockerfile CMD into `gunicorn.conf.py`. `GUNICORN_BIND` accepts a comma-separated list of listen addresses (default unchanged: `0.0.0.0:5000`); rootless podman users wanting dual-stack can set `GUNICORN_BIND=[::]:5000`. `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, and `GUNICORN_LOG_LEVEL` are also overridable now.

## [2.6.64] - 2026-07-09

### Added

- **Rolling-integrity coverage is now visible.** Because budgeted integrity runs sweep the library in slices, no single run report answers "has every file been verified?". `/api/stats` now returns an `integrity` block - files checked ever and within the last 30 days, percent of library covered, never-checked count, the oldest check date (every checked file has been verified at least once since then), and the current bitrot-suspected count. The dashboard gains an "Integrity Checked" stat card showing the coverage percentage, with the full breakdown in its tooltip.

## [2.6.63] - 2026-07-09

### Fixed

- **Created or edited schedules now take effect within a minute instead of requiring a container restart.** Schedule CRUD dispatched `reload_schedules_task` through Celery, but prefork workers execute tasks in pool children while APScheduler runs only in the scheduler-lock-holding main process - the reload always landed in a child, logged "Scheduler not running in this worker", and was skipped, so no schedule change since the mechanism was added ever reached the running scheduler without a restart (verified live: a freshly created `file_changes` schedule never fired at its cron time). The scheduler process now re-syncs its jobs directly from the database every 60 seconds, gated on a fingerprint of the schedule definitions so jobs are only rebuilt when something actually changed. New schedules also get `next_run` computed at creation so the UI shows the upcoming fire immediately instead of after the first run.

## [2.6.62] - 2026-07-09

### Added

- **Bitrot UI.** Results list shows a distinct "Bitrot?" badge (violet) taking precedence over corrupted/warning; a "Bitrot Suspected" filter button joins the corrupted/warning/healthy set (`bitrot_suspected=true` on `/api/scan-results`); the file details modal gains a bitrot section (detection date, baseline vs current hash/mtime/size, stable-check count) with an "Accept Current State" action; the same accept action appears in each flagged file's Actions dropdown (desktop and mobile), confirming before adopting the current content as the new baseline via `POST /api/bitrot/accept`.

## [2.6.61] - 2026-07-09

### Added

- **The integrity check now distinguishes bitrot from legitimate modification.** Previously any hash mismatch was uniformly marked `modified` and rescanned - and the rescan adopted the new content's hash as baseline, silently laundering corruption (a bit flip in non-critical stream data passes decode checks). The hash task now classifies: hash mismatch with CHANGED mtime = `modified` (normal edit/replace, rescans as before); hash mismatch with UNCHANGED mtime (2-second epsilon) = `bitrot_suspected` - no legitimate write path alters content without touching mtime. Suspected files are flagged (`bitrot_suspected` + permanent `bitrot_detected_date`/`bitrot_details`, first detection date preserved across re-detections), one aggregated high-priority `bitrot_suspected` notification fires per run (a dying disk flags files in bulk; per-file dispatch would mean thousands of serial HTTP round-trips), the file is rescanned for decodability, and critically NO scan path overwrites the stored hash/mtime baseline for flagged files: the guard lives in a single shared writer (`apply_scan_baseline`) used by both the serial scan save and the parallel chunk workers that perform all normal-scan and rescan persistence.
- **Auto-expire state machine for flagged files.** Flagged files jump the integrity queue. Each subsequent check compares against the candidate hash: stable content for `BITROT_STABLE_CHECKS_TO_EXPIRE` consecutive checks (default 2) plus a clean rescan auto-adopts the content as the new baseline and clears the flag (detection record kept permanently - a per-disk history of "files that ever tripped bitrot" is a dying-disk signal). Content matching the ORIGINAL baseline again = transient read anomaly, flag clears. A THIRD hash = active rot: counter resets, re-notification fires. Manual override: `POST /api/bitrot/accept` (file_ids) adopts the current state immediately via the same shared transition as auto-expire; the adopted baseline mtime is the one recorded with the candidate hash, not a fresh stat, so the stored hash/mtime pair always describes content that actually existed. Stale queued results for a row whose flag was cleared mid-run (accept races Phase 3 on budgeted runs) are ignored instead of resurrecting state-machine fields.
- **Notification rules now actually fire.** `NotificationRule`/`NotificationProvider` existed as CRUD-only models - no backend code ever evaluated rules. New `dispatch_event()` in the notification service joins active rules to active providers, delivers, and records per-provider status. `bitrot_suspected` is the first wired event type.
- `bitrot_suspected` filter on `/api/scan-results`; bitrot columns in CSV/JSON exports and `ScanResult.to_dict`.

### Fixed

- **`get_file_info` stored naive local mtimes; all baselines are now UTC.** `datetime.fromtimestamp(st_mtime)` without a timezone poisoned every stored `last_modified` on hosts where TZ != UTC (its error fallback even stored aware UTC, mixing both semantics in one column). Bitrot classification requires trusted baselines, so a new `mtime_baseline_utc` flag (false for all pre-upgrade rows) gates it: untrusted rows whose hash mismatches classify as `modified` and re-baseline via rescan, and untrusted rows whose hash MATCHES (the cold-archive common case) re-baseline directly during the integrity check - no false bitrot alarms from the old data, detection reliable from each file's second check. The scan-result cache validity check now treats a content-hash match as sufficient (mtime equality only as fallback for hash-less rows), so the UTC change cannot force a one-time full-library re-decode on hosts where TZ is not UTC. Scan error paths no longer fabricate a scan-time mtime for unreadable files; they leave the stored baseline untouched.

## [2.6.60] - 2026-07-09

### Added

- **Integrity check schedules can now carry a time budget (`time_budget_minutes`).** A budgeted run dispatches hash tasks until the soft deadline expires, drains in-flight tasks (which still stamp `last_integrity_check_date`), and completes with a coverage message ("Budget reached: N of M files verified this run"). Because the rolling queue (2.6.59) is ordered stalest-first, successive budgeted runs sweep the whole library in disjoint slices - a 50-hour full pass becomes ~100 min/day for a monthly cadence - without any single run monopolizing IO. NULL budget = unlimited (current behavior); the field is valid only on `file_changes` schedules and manual runs stay unlimited unless the start request passes `time_budget_minutes` explicitly. Budgeted runs dispatch largest files first within each batch so a 70GB file is not started at minute 59; dispatch skips past saturated size tiers instead of head-of-line blocking, so a run of large files at the front of a batch cannot cap the whole window at the huge-file concurrency limit while small-file slots idle. The budget is resolved inside the service (not the HTTP route), so every entry point honors a schedule's configured budget. Schedule create/edit forms expose the field with sizing guidance.

## [2.6.59] - 2026-07-09

### Changed

- **The integrity check (file changes) is now a rolling queue instead of a single full-table pass.** The producer previously loaded every `scan_results` row into memory in one query (hundreds of MB at 1M+ files) and lost all progress on crash or cancellation. It now re-fetches the queue in ordered batches (`INTEGRITY_BATCH_SIZE`, default 10,000): never-checked files first (`last_integrity_check_date` NULL), then stalest-first. `last_integrity_check_date` - previously written but never read - is now the queue cursor: every terminal outcome (result received, hash error after retries, abandoned task) stamps the file, pushing it to the back of the ordering, so an interrupted run resumes where it left off by construction. In-flight tasks, rows whose timestamp write failed, and rows whose task dispatch failed (broker errors) are excluded from re-fetches explicitly - ordering alone would re-hash the first two and re-fail the third forever; failure counts are surfaced in the completion message. Stamps are written in bulk (one UPDATE per batch fetch instead of one SELECT plus UPDATE per file), a queue re-fetch only runs after a dispatch, completion, or exclusion changed the queue (no tight query loops when every size tier is saturated or the broker is down), and a successfully hashed file gets `file_exists` restored, recovering rows stranded by a past mount outage. A composite index supporting the queue ordering ships in the same migration.

### Fixed

- **Producer no longer accumulates one AsyncResult handle per dispatched task.** `task_results` collected every dispatched task's result handle for the lifetime of the run and was never read - a per-run memory leak of 1M+ objects on large libraries. Removed.

## [2.6.58] - 2026-07-09

### Removed

- **Legacy serial `MediaChecker.check_file_changes` deleted.** It had zero callers (all file-changes routes go through `MaintenanceService`'s Celery-based path) and was a divergent second implementation: it only hashed a file when its mtime differed, so it structurally could not detect bitrot (content change with unchanged mtime); it compared naive local timestamps; and it ran serially with no progress reporting or cancellation. Removing it ahead of the bitrot classification work ensures the integrity-check logic exists in exactly one place.

## [2.6.57] - 2026-06-11

### Fixed

- **The dispatch pub/sub no-op now actually applies to the threads that dispatch - the 2.6.55/2.6.56 fix never took effect in production.** Celery's `Celery._backend` is stored in a `threading.local` unless `result_backend_thread_safe` is set, so every thread gets its OWN backend instance. The no-op was patched onto the backend created on the import thread, while the cleanup/integrity producer threads (spawned per run by the maintenance routes) lazily created fresh, unpatched backends and kept subscribing - verified live: the first 2.6.56 cleanup run still showed 3,000-5,000 standing subscriptions and ~70-second Redis forced disconnects. Root-caused with a local reproduction (the run is clean when driven from the main thread, subscribes when driven from a `threading.Thread`, confirmed via a stack trace through `send_task -> backend.on_task_call`). `disable_dispatch_result_subscription()` now wraps `Celery._get_backend` so every backend instance created by any thread carries the no-op; validated end-to-end in a local stack where the API-triggered cleanup previously accumulated 2,633 result-channel subscriptions and now accumulates zero. The regression test now asserts the no-op holds in a freshly spawned thread. This retroactively explains all three production runs today: 2.6.53 stalled (get() subscriptions plus dispatch subscriptions), 2.6.54 was clean (its per-dispatch `cancel_for` ran on the same thread-local objects that subscribed), and 2.6.56 regressed (dispatch subscription alive in the producer thread with the unsubscribe removed).

## [2.6.56] - 2026-06-11

### Fixed

- **Boot regression from 2.6.55 fixed; that release is yanked.** 2.6.55 added an uppercase `CELERY_RESULT_BACKEND` attribute to `Config`, but Celery treats that name as an OLD-style setting key, so `celery.conf.update(app.config)` raised `ImproperlyConfigured: Cannot mix new and old setting keys` and gunicorn workers failed to boot (production down ~6 minutes, rolled back to 2.6.54 via webhook). `CELERY_BROKER_URL` never triggered this because the old-style broker key was `BROKER_URL`.
- **The Flask app's Celery instance now reads the correct result backend URL in containers - reimplemented safely.** The underlying bug from the 2.6.55 attempt is real: `config.py` exposes only the lowercase `result_backend` attribute, which Flask never copies into `app.config`, so the app-bound Celery instance silently defaulted its result backend to `redis://localhost:6379/0` and every `AsyncResult.state` check in scan routes and the scheduler failed against a nonexistent local Redis. `create_celery()` now falls back to the `CELERY_RESULT_BACKEND` environment variable for the app path, keeping old-style keys out of `app.config`. A regression test asserts no uppercase `Config` attribute is in Celery's `_OLD_SETTING_KEYS` and that conf finalization succeeds against the full app config.

## [2.6.55] - 2026-06-11 [YANKED]

Image pushed and briefly deployed, then rolled back: gunicorn workers failed to boot (see 2.6.56). The pub/sub fix below is sound and ships in 2.6.56.

### Fixed

- **Producer dispatches no longer open a result pub/sub connection at all, eliminating the recurring Redis forced-disconnect cycle.** The 2.6.54 fix removed the stall mechanism (verified: 24-minute cleanup run, zero stalls), but Redis still force-closed the producer's pub/sub connection every ~3 minutes during runs: `apply_async` subscribes to each task's result channel (`backend.on_task_call`), and the subscribe/unsubscribe replies themselves accumulated unread in the socket's output buffer. The result backend's dispatch-time subscription hook is now disabled for app instances (`disable_dispatch_result_subscription` in `utils/celery_utils.py`) - producers consume results via stored meta and need no subscription, and `result.get()` users are unaffected because `get()` subscribes itself and reconciles from stored meta. The now-redundant per-dispatch `unsubscribe_result()` from 2.6.54 was removed. Also saves ~1,600 useless Redis ops/s during maintenance runs.

## [2.6.54] - 2026-06-11

### Fixed

- **Cleanup/integrity producer loops no longer stall for minutes on Redis pub/sub disconnects.** With the checkpoint storms fixed (2.6.53), residual multi-minute stalls remained where workers sat idle. Root cause: Celery's Redis result backend subscribes the producer to each task's result pub/sub channel - once at dispatch (`on_task_call` inside `apply_async`) and again inside `AsyncResult.get()`. The producer loops never read that socket, so every completed task's published result accumulated in its output buffer until Redis force-closed the connection (default pubsub client-output-buffer-limit, observed every ~70 seconds during the verified cleanup run); reconnect/resubscribe churn mid-pass over the 5,000-task window froze the loop for up to 7 minutes per incident. Two-part fix in the shared producer helpers: the dispatch-time subscription is cancelled immediately after `apply_async` (new `unsubscribe_result()`), and `safe_task_get()` now consumes results via the stored meta (`task.state`/`task.result`, cache-served after the `ready()` gate) instead of `get()` - same success/failure/timeout semantics, no subscription, no extra Redis round-trips. Applies to both orphan cleanup (Phase 2) and file-changes/integrity scans. The orchestrator's discovery join keeps `get()`: it genuinely waits on a handful of per-directory tasks and drains its socket while waiting.

## [2.6.53] - 2026-06-11

### Fixed

- **Maintenance runs no longer trigger 5-minute checkpoint IO storms.** During the verified cleanup run, recurring 2-7 minute slowdowns aligned to the second with PostgreSQL checkpoint write phases. Cause: `celery.app.trace` logs one INFO row per task ("succeeded in Xs" with the full result dict), and the database log handler stored all of them - ~150-190MB of WAL per 5 minutes at ~850 tasks/s, forcing heavy checkpoints that stalled the producer's own DB operations. `celery.app.trace` is now in the default log-exclusion list, and a startup migration appends it to the stored config on existing installs (additive - user customizations survive). Container/Loki logging is unaffected.

## [2.6.52] - 2026-06-11

### Fixed

- **Maintenance runs no longer flood Redis with a million result keys.** Task results were never forgotten and expire only after 24h, so a full-library cleanup or integrity run accumulated 1M+ result keys in the noeviction Redis - observed live as every broker/backend operation slowing ~100x once roughly 1M files had been processed (heartbeats minutes apart in the final stretch, "0 active tasks" snapshots, misleading "ETA: 0m"). Both maintenance loops now forget each result after consuming it (and on abandonment), bounding Redis to in-flight tasks only (~5,000 keys instead of 1.18M).
- **Scan report details no longer render "[object Object]" rows.** Cleanup and file-changes reports store file lists (objects with file_path/change_type) in the directories field; the report modal now renders those as labeled file lists (Orphaned Files / Changed Files, capped at 100 entries) instead of joining raw objects.
- Cleanup ETA under one minute reads "<1m" instead of "0m".

## [2.6.51] - 2026-06-11

### Fixed

- **Orphan cleanup no longer loads 1.18M full ORM objects into the web worker.** Phase 1 used `ScanResult.query.all()` - several GB of resident memory inside the gunicorn worker hosting the cleanup thread, the most likely driver of the original silent-stall symptoms (memory pressure makes every loop iteration glacial, so the producer looks blocked, cancel windows get missed, and nothing logs). The loop reads exactly two fields, so Phase 1 now loads `(id, file_path)` tuples via `with_entities` - roughly a 20-30x memory reduction at 1.18M files. The file-changes flow already loaded lean tuples; cleanup was the only full-ORM holdout.

## [2.6.50] - 2026-06-11

### Fixed (startup wedge during app-only container updates)

- **Startup can no longer hang forever behind a blocked migration.** Observed live: an app-only container update (worker kept running) wedged the entire boot - the v2.6.0 migration ran an unconditional `ALTER TABLE app_configs ... SET DEFAULT` on every start, which needs an ACCESS EXCLUSIVE lock; the still-running worker container held idle-in-transaction locks, the ALTER waited forever, and all gunicorn workers sat behind the migration step serving nothing. All migration connections now apply fail-fast timeouts via `SET LOCAL` (`MIGRATION_LOCK_TIMEOUT_MS`, default 10s; `MIGRATION_STATEMENT_TIMEOUT_MS`, default 300s) so a blocked migration logs an error and the boot continues - migrations are idempotent and retry next start. `SET LOCAL` scopes the timeouts to the migration transaction; a plain `SET` would survive the connection's return to the pool and impose migration timeouts on unrelated app queries. The advisory-lock waiter path is bounded by the same statement timeout (it was the last remaining infinite wait), and the v2.6.0 ALTER is now conditional (only runs when the defaults are actually missing), so it stops taking an exclusive lock on every boot.
- **The app container restarts itself again.** The `pixelprobe` service had no restart policy (only the worker did) - a killed or crashed boot stayed dead. Added `restart: unless-stopped` and raised the healthcheck `start_period` to 120s so migration time is not counted against liveness.
- Misleading "Created X table via migration" log lines (emitted unconditionally for `CREATE TABLE IF NOT EXISTS`) now read "Ensured X table exists".

### Changed

- Cleanup Phase 2 progress is written by the 10-second heartbeat only (the per-100-file write added in 2.6.49 would have produced ~12,000 commits per full run for no UI benefit). Task abandonment is now a shared `abandon_if_stuck()` helper used by both the cleanup and file-changes loops, and integer env knobs parse through one `env_int()` utility.

### Diagnosis notes (cleanup "stuck" reports on 2026-06-10)

- The recurring frozen progress display ("Checking 4,999 / 1,187,442") was the 2.6.49 modulo display bug on a container still running the first 2.6.49 build - the worker was processing existence checks at full rate the whole time. Same-tag image rebuilds do not redeploy via the Portainer webhook; this release's version bump exists to force a clean recreate with all fixes aboard.

## [2.6.49] - 2026-06-10

External code audit remediation (21 findings: 4 High, 9 Medium, 8 Low) plus convergence of the two scan engines onto the chunk-distributed path.

### Changed (scan engine convergence - WORKER-2)

- **`/api/scan` now runs the chunk-distributed engine.** Directory scans are split into disjoint path-range (FCP) chunks and fanned out as `process_chunk_task` across ALL Celery worker slots, instead of a thread pool inside one Celery task. Scan throughput now scales with `CELERY_CONCURRENCY` (8-12 recommended; was bounded by `MAX_WORKERS` threads in one slot). `MAX_WORKERS` still applies to selected-file rescans.
- **Scan completion uses last-chunk-finalizes.** Every chunk exit (success, empty, cancelled, error) marks its chunk terminal; the last one finalizes the scan under a PostgreSQL row lock (exactly-once): computes per-scan totals from chunk sums, reclaims stuck `'scanning'` rows, creates the ScanReport, and sends the scheduled-scan healthcheck ping. A scan with failed chunks finalizes as `'error'` (failure ping, error message naming the chunk count) instead of claiming a clean completion. The stuck-scan sweeper finalizes as a backstop if the winning chunk dies at the wrong moment; it never touches legacy-engine (selected-file) scans or scans without chunks.
- **Chunk task retries are loss-free.** A retried chunk reclaims its claimed range back to pending before retrying (otherwise the retry would find nothing and report the chunk complete with 0 files), and a failed batch commit propagates into the retry instead of silently dropping up to 100 files' results.
- **Discovery is parallel and memory-safe.** One discovery task per directory bulk-inserts pending rows (ON CONFLICT DO NOTHING) and returns counts only - file lists no longer round-trip through the Redis result backend (~100MB+ at 1.2M files). The discovery-incompleteness guard is preserved: any truncated walk aborts the scan instead of reporting a partial library as complete. Discovery timeout default raised 120s -> 3600s and is now env-configurable (`DISCOVERY_TASK_TIMEOUT_SECS`); the old 120s default would have aborted every production-scale scan.
- **`/api/scan-parallel` is now a deprecated alias of `/api/scan`** (same engine, same claim). `/api/force-scan-pending` and the directories branch of `/api/scan-files-parallel` also route through the converged launcher. The Celery-unavailable thread fallback is gone: launches return 503 and release the claim instead.
- **Behavioral deltas:** files are scanned in path order (newest-first ordering of new files is dropped); the `adding` phase counts DB inserts during discovery rather than a separate pass; `file_type` is populated by the scan, not discovery (dropping ~1.2M libmagic reads from the discovery phase - newly discovered rows briefly show `unknown`); a single-file rescan no longer deactivates an actively progressing scan (`create_new_scan` only clears scans stale for 30+ minutes); the scan-start claim now blocks on an active scan in ANY scan-state row (single-file rescans included) and resets its staleness markers so the sweeper cannot kill a freshly queued scan; failed scan starts surface the server's reason in the UI (e.g. "Celery workers not available").

### Fixed

- **Validation failures no longer wedge scanning (SCHED-1, High).** `/api/scan` validated directories AFTER committing `is_active=True`, so a 400 (bad directory, none configured) left the claim stuck and every later scan got 409 until the 30-minute sweeper. Validation now happens before the claim, and any post-claim launch failure releases it.
- **Parallel engine tasks were never registered with the worker.** `celery_config.py` only included `pixelprobe.tasks`, so `/api/scan-parallel` dispatched tasks the worker discarded as unregistered - the parallel engine had never actually run. `pixelprobe.tasks_parallel` is now in the worker's include list.
- **Deleted the UI heartbeat task that occupied a worker slot all scan long (WORKER-1, High).** `ui_progress_update_task` busy-polled the DB at 0.5s for the entire scan, consuming 25% of scan capacity at `CELERY_CONCURRENCY=4`, and could itself be starved into triggering false stuck-scan detection. Chunk tasks now bump `last_update` every 100 files or 60 seconds, whichever comes first (sweeper-safe even on multi-minute video files).
- **Path exclusions and chunk queries no longer over-match sibling directories (SCHED-2/WORKER-3, High/Medium).** Excluding `/media/movies` also excluded `/media/movies2`; `LIKE 'dir%'` chunk/cleanup queries had the same flaw (cross-chunk double-claims, widened deletion sets). New `pixelprobe/utils/paths.py` helpers (`is_path_under`, `like_prefix` with SQL wildcard escaping) replace all 14 sites; FCP range chunks eliminate the in-engine cases by construction.
- **Per-file DB round-trips removed from chunk scanning (WORKER-5/EFF-1).** Chunks claim their whole disjoint range with one `UPDATE ... RETURNING` and write results on ORM objects loaded in batches of 100 (was up to 3 queries per file across claim/write/error).
- **Empty chunks no longer stomp live progress (WORKER-4).** The empty-chunk path stops writing `current_file`/`progress_message` on the shared scan state mid-scan.
- **`force_rescan` is scoped to the requested directories.** The old orchestrator reset every completed row in the database; now only rows under the requested directories are reset (boundary-safe LIKE).
- **Orchestrator exclusion crash fixed.** It read `Exclusion.pattern`, an attribute that does not exist (AttributeError on any active exclusion row), and used the DB table instead of the canonical `exclusions.json`. Discovery now uses `load_exclusions_with_patterns()` including filename patterns.
- **Per-scan completion totals (WORKER-7 adjacent).** The never-dispatched `scan_completion_monitor` computed "files processed" from a full-table count across all scans ever; the finalizer sums this scan's chunks.
- **Scheduler duplicate-schedule cleanup commits once after iteration (SCHED-3, Low)** instead of committing inside the loop over live query results.
- **Interval schedule fire-time is computed before the job is added (SCHED-4, Low).** Extracted `compute_next_interval_run()` (unit-testable branches: future-from-last-run, stored-future, past -> now+interval, no history) and passed into `add_job` directly, removing the add-then-modify window where a job could fire at the wrong time.
- **Orphan cleanup can no longer wedge silently at max concurrency.** Observed live: cleanup froze at 20,000/1,187,442 files with idle workers and zero log output - un-ready task handles were retried forever with no timeout, and progress only wrote when the counter landed on an exact multiple of 100. Cleanup Phase 2 now mirrors the file-changes hardening: per-task `submitted_at` tracking with abandonment after `CLEANUP_TASK_TIMEOUT_SECS` (default 600s; abandoned files are counted unverifiable and NEVER deleted), a 10-second progress heartbeat with a log line, and delta-based progress writes instead of the modulo check.

### Security

- **Argument-injection hardening for media tools (SEC-1, High).** The shell-metacharacter blocklist in `validate_command_args` was security theater (inert with `shell=False`) while real risks went unaddressed: `ffprobe` and ImageMagick `convert` received file paths in option position, and two `ffmpeg`/`ffprobe` call sites bypassed `safe_subprocess_run` entirely. Added `ensure_cli_safe_path()` ('./'-prefix for relative paths; `convert` has no `--` separator), applied it at the vulnerable sites, routed the raw `subprocess.run` calls through the safe wrapper, and narrowed `validate_command_args` to null bytes/newlines with an honest docstring. Also fixed the null-byte pattern, which matched the literal text `\x00` instead of an actual null byte.
- **Legitimate filenames are scannable again (SEC-5, Low).** `validate_file_path` rejected any path containing `~`, `$`, or `%` - all legal in real media filenames. Containment is enforced by realpath + `safe_join` against the allowlist; the blocklist now rejects only traversal tokens and control characters.
- **Container healthcheck works (SEC-2, Medium).** New unauthenticated `/healthz` liveness endpoint (no DB ping, so a DB blip cannot restart-loop the container); compose healthcheck points at it. `/health` stays authenticated.
- **CORS is opt-in (SEC-3, Medium).** The blanket `origins: "*"` on `/api/*` is gone; set `CORS_ORIGINS` (comma-separated) only if cross-origin browser access is needed. Same-origin UI, server-side scheduler calls, and API tokens are unaffected.
- **Session cookies default to Secure (SEC-4, Medium).** Plain-HTTP LAN deployments must set `SESSION_COOKIE_SECURE=false`.
- **Rate limits are now cluster-wide (OPS-1, Medium).** Flask-Limiter counters move from per-process memory (each of 4 gunicorn workers kept its own, multiplying every limit by 4) to Redis (broker host, DB 1; `RATELIMIT_STORAGE_URI` to override), with in-memory fallback if Redis is down.
- **Compose no longer exposes datastores (SEC-6/OPS-2, Low).** PostgreSQL 5432 and Redis 6379 host port publications removed (inter-container traffic uses the compose network; Redis also holds the broker and internal API secret). `POSTGRES_PASSWORD` is now required - the `changeme` default is gone. Redis `requirepass` wiring is noted as future work.
- **Connection pool can no longer exceed PostgreSQL `max_connections` (DB-1, Medium).** `pool_size`/`max_overflow` reduced 20/40 -> 5/10 per process (env: `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`): web worst-case drops 240 -> 60 connections; compose comment now states the real math.

### Removed

- `ScanService` thread-pool engine internals (`scan_directories`, `resume_scan` and friends, ~1,600 lines) - replaced by the chunk engine; `scan_media_task` remains as a shim so messages queued across the deploy still execute. `scan_single_file`/`scan_files` (single/selected-file rescans) are unchanged.
- `scan_completion_monitor` (never dispatched), `cleanup_orphaned_task` (stub returning fake success; real cleanup goes through MaintenanceService), `scheduled_scan_task` (never dispatched), `ui_progress_update_task` + `_final_sync_redis_to_db`.
- `pixelprobe/database/connection_manager.py` (DB-2, Medium): dead code with a broken multi-yield contextmanager that would raise `RuntimeError` if ever used.
- Dead `worker_max_tasks_per_child=50` in `celery_config.py` (EFF-2; the CLI flag in `celery_worker.py` wins).
- Compose image pin to stale 2.5.70 (OPS-3) - now `${PIXELPROBE_VERSION:-2.6.49}`.

### Deployment notes

- New env knobs: `DISCOVERY_TASK_TIMEOUT_SECS` (default 3600), `DB_POOL_SIZE` (5), `DB_MAX_OVERFLOW` (10), `RATELIMIT_STORAGE_URI`, `CORS_ORIGINS` (empty = no CORS), `SESSION_COOKIE_SECURE` (default true).
- Compose now requires `POSTGRES_PASSWORD` to be set explicitly.
- Recommend raising `CELERY_CONCURRENCY` to 8-12: it is now the main scan-throughput knob.

## [2.6.48] - 2026-06-01

### Fixed (integrity scan reliability at scale: 1.2M files up to ~55GB)

- **Celery worker no longer crashes mid-scan with `Unrecoverable error: AssertionError()`.** Under a high-throughput integrity scan (1M+ tiny immich thumbnails hashing in sub-millisecond bursts) the worker event stream flooded the kombu event-loop hub and tripped an assertion in `hub.fire_timers`, wedging the worker so every subsequent task was abandoned at the timeout and the scan got stuck. Disabled Celery task events (`worker_send_task_events` / `task_send_sent_event`; PixelProbe reads progress from the DB, not Celery events) and launched the worker with `--without-gossip --without-mingle --without-heartbeat` to remove the worker-to-worker event-loop timers (unused with a single worker on a Redis broker) that fed the crash.
- **Reduced the fork storm on tiny-file scans.** Raised the default `--max-tasks-per-child` from 50 to 1000 (env `CELERY_MAX_TASKS_PER_CHILD`); 50 sub-millisecond hashes recycled a child in well under a second, producing tens of thousands of forks that hammered the pool. Per-child memory is still capped at 2GB for heavy/large-file tasks.
- **Worker now auto-restarts after an unrecoverable Celery shutdown.** `celery_worker.py` exits non-zero when `worker_main` returns unexpectedly, so `restart: unless-stopped` brings the container back instead of leaving a dead/idle worker.
- **The largest files are no longer skipped.** The integrity per-task timeout default was raised from 30 minutes to 3 hours (env `INTEGRITY_TASK_TIMEOUT_SECS`). The hash task reads the whole file; a ~55GB sequential read on slow/contended storage can exceed 30 minutes, which previously abandoned (skipped) the biggest files mid-hash.

## [2.6.47] - 2026-06-01

### Fixed (reliability hardening - phase 4: observability + cleanup)

- **Idempotent healthcheck pings now retry transient failures.** `create_safe_session` mounts a urllib3 `Retry` adapter (3 attempts, exponential backoff, retries connection errors + 429/5xx) for GET requests, so a momentary blip no longer drops a GET healthcheck ping (which could trip a false "down" alert). POST is deliberately not retried so a notification/webhook the provider already delivered before returning 5xx is not re-sent as a duplicate.
- **Status endpoints no longer report "idle" on a query error.** `/api/cleanup-status` and `/api/file-changes-status` now return HTTP 500 with `status_unavailable` (rolling back the session) instead of `is_running: false` with 200, so the polling UI does not mistake a transient error for "operation finished" and start a duplicate.
- **API-token `last_used` write is throttled.** Updated at most once per 5 minutes per token (was a DB write+commit on every authenticated request), and rolled back on failure so a failed write does not leave the request's session dirty.
- **Background maintenance uses the real database.** Cleanup and file-changes async workers now construct `MaintenanceService` from the app's `SQLALCHEMY_DATABASE_URI` instead of the `DATABASE_URL` env var that is unset in the PostgreSQL-only deployment (which fell back to a stray sqlite file).
- **Streaming log download releases its cursor.** The `/api/logs` download generator closes the DB session in a `finally`, so a client disconnect mid-download no longer pins the streaming cursor / pooled connection.
- **Data-retention cleanup actually runs.** It is now scheduled by the single-leader `MediaScheduler` (daily at 04:00) instead of a Celery `beat_schedule` that was configured but never launched (no beat process ran), so the retention task no longer silently never executes. The dead `beat_schedule` entry was removed.

## [2.6.46] - 2026-06-01

### Fixed (reliability hardening - phase 3: session hygiene + cross-process coordination)

- **Aborted DB sessions are rolled back instead of left poisoned.** `ScanState.get_or_create`, `ScanState.update_progress`, the scheduler's `_load_saved_schedules`, and the auth API-token `request_loader` now roll back on a swallowed exception so a failed statement does not leave the scoped session in an aborted state that breaks later queries. `update_progress` no longer double-commits (the success path commits once in its retry loop; the failure path makes a single best-effort `is_active` correction) so a failed progress write can no longer be masked by a second commit on a rolled-back instance.
- **An app-container restart no longer crashes a scan still running in the worker container.** `cleanup_stuck_scans` previously marked ALL active scans crashed on every boot; since the web app and Celery worker run as separate containers, that wrongly killed in-flight worker scans on an app restart. It now only crashes scans with no recent progress (`last_update` older than `STUCK_SCAN_STARTUP_GRACE_SECS`, default 30 min); actively-progressing scans survive the restart.
- **Concurrent scan starts can no longer double-start.** `/api/scan-parallel` now uses the same atomic `with_for_update(nowait=True)` claim as `/api/scan` (reusing the claimed `scan_id` so the orchestrator does not create a duplicate row, and releasing the claim if the launch fails). `/api/cleanup-orphaned` and `/api/file-changes` now serialize their start with a PostgreSQL transaction-scoped advisory lock (a no-op on the sqlite test DB) to close the SELECT-then-INSERT TOCTOU that allowed two workers to both spawn the operation.
- **Non-critical startup steps no longer crash every worker.** The log-handler, scan-path sync, scheduler init, and stale-state cleanup steps in app startup are each wrapped so a single failure is logged and does not abort the import for all gunicorn workers; core DB service init still fails fast.

## [2.6.45] - 2026-06-01

### Fixed (reliability hardening - phase 2: silent / dropped work)

- **Scheduled scans no longer silently drop a cron fire.** Closes the gap behind the missed Integrity scan: (a) when `scan_lock` is held by another schedule firing at the same instant, the loser now queues a retry instead of returning; (b) `_execute_scan_request` returns its status and a shared `_handle_scan_response` queues a retry on a 409 (a scan slipped into the check->POST window) or a connection error -- now unified across scheduled, periodic, and cleanup fires; (c) `last_run`/`next_run` advance only after a confirmed HTTP 200 (on a freshly re-fetched row), so a failed fire is retried rather than recorded as a successful run that skips a full interval; (d) the healthcheck start ping is sent only after a confirmed start, so a conflicted/failed fire no longer leaves a dangling "start" that trips a false down-alert; (e) cron/interval jobs now set `misfire_grace_time=3600` and `coalesce=True` so a fire missed during a brief scheduler restart still runs. The retry counter is cleared on benign no-op fires (no paths / disabled schedule) so it cannot drift upward.
- **Parallel discovery no longer reports completion on a truncated file set.** `discover_directory_task` now returns a structured `{files, complete, error}` result; a walk timeout or error marks the directory incomplete. The orchestrator aborts the scan with an error (instead of scanning a partial set and reporting it complete) when a discovery target did not finish; a spurious result-collection error where every task actually completed does not abort. The discovery wait is env-overridable via `DISCOVERY_RESULT_TIMEOUT_SECS` (default 3600s).
- **A file that cannot be hashed is no longer reported as unchanged.** `calculate_file_hash_task` now returns `changed=True` (change_type `error`) after exhausting retries, so the file is re-examined by a full scan instead of being silently treated as unchanged -- which previously masked the corruption the integrity check exists to catch.
- **Incomplete deep media checks are surfaced.** Swallowed exceptions in the temporal-outlier and multi-point sampling checks are now logged at warning level (and multi-point sampling adds a "did not complete" warning detail) instead of being hidden at debug level, so a deep check that did not run is visible rather than implicitly passing.

## [2.6.44] - 2026-06-01

### Fixed (reliability hardening - phase 1: data loss and permanent wedges)

- **Orphan cleanup could delete valid database rows when a mount flapped.** `check_file_exists_task` used `os.path.exists()`, which swallows `OSError` and returns `False`, so a down/stale NFS mount made every file read as absent and orphan cleanup irreversibly deleted its `ScanResult` row. Existence is now classified as a tri-state (`exists` / `absent` / `unknown`) via the new `classify_path_existence()` helper, which uses `os.stat()` (raises on error) and only treats `ENOENT`/`ENOTDIR` as absent; transient/IO errors return `unknown` and are skipped, never deleted. `maintenance_service` now also aborts the whole cleanup (deleting nothing) when an implausibly large fraction of checked files read as absent (defaults: >=100 files and >=50%, env-overridable via `ORPHAN_CLEANUP_ABORT_FLOOR` / `ORPHAN_CLEANUP_MAX_DELETE_FRACTION`), as a safety net against a disappeared mount.
- **ffprobe metadata reads could hang a scan worker forever.** `ffmpeg-python`'s `probe()` calls `Popen.communicate()` with no timeout, so a stalled mount or crafted container permanently consumed a scan thread. Probing now goes through the new `_ffprobe_with_timeout()` helper (env-overridable `FFPROBE_TIMEOUT_SECS`, default 120s) backed by `safe_subprocess_run`; a timeout flags the file rather than wedging the worker.
- **Subprocess timeouts now isolate the child session.** `safe_subprocess_run` starts media subprocesses with `start_new_session=True` (POSIX) so subprocess.run's timeout-kill targets the child without signalling PixelProbe's own process group. (The audio probe path now also handles the resulting `TimeoutExpired`, so an ffprobe timeout flags the file instead of raising.)
- **Scheduler distributed-lock heartbeat no longer abandons the lock on a Redis blip.** The heartbeat thread previously `break`-ed out of its loop forever on the first exception, silently abandoning the 60s-TTL lock. It now retries indefinitely; when Redis recovers, the next refresh re-establishes the TTL. A full Redis outage expires the lock for every container (no sibling can acquire it either), so this process simply resumes ownership on recovery -- it does not shut the scheduler down, since a `BackgroundScheduler` cannot be restarted and a single-container deployment has no sibling to take over.
- **Files left stuck in `scanning` by a dead worker are now reclaimed.** A chunk marks files `scanning` before processing; if its worker died they were never re-selected (retry only picks `pending`) and the scan reported complete with them unscanned. The stuck-scan check (every 5 min) now resets orphaned `scanning` rows to `pending` whenever no scan is active. The stuck-scan handler also rolls back its session on error.

## [2.6.43] - 2026-05-25

### Security

- **Bump `urllib3` floor to `>=2.7.0`** to resolve CVE-2026-44431 and CVE-2026-44432 (HTTP response decompression denial-of-service). HIGH severity. Pinned in `requirements.txt` because it's transitive through `requests`; `requests==2.33.0` still permits the vulnerable 2.6.x line otherwise.
- **`apt-get upgrade` added to the Ubuntu 24.04 base layer** so the image picks up security patches the base image hasn't been rebuilt with, including the linux-libc-dev kernel-header CVEs (CVE-2026-22984, CVE-2026-31431) that Trivy flagged at HIGH.

### Fixed

- **Weekly schedule silently dropped when a long-running AddNew scan was concurrent**. On 2026-05-25 the Sunday 02:00 EDT cleanup (`schedule_10`) was skipped six times in a row and then abandoned because the 6-hour AddNew scan that started at 00:00 took 3h 5m (vs the typical <35 min) and was still in `phase=scanning` at 03:00 -- the queue-on-conflict retry window introduced in v2.6.39 was capped at `DEFAULT_RETRY_MAX_COUNT=6 * DEFAULT_RETRY_DELAY_MINUTES=10 min = 60 min`. The conflicting scan finished four minutes after the cap expired, so the weekly schedule was lost until the next cron fire one week later. Raised `DEFAULT_RETRY_MAX_COUNT` from 6 to 144, giving a 24-hour retry window that safely covers any realistic scan duration. Per-schedule retry counters (`pending_retries[f"schedule_{schedule_id}"]`) remain isolated, and the env override `SCHEDULE_RETRY_MAX_COUNT` is unchanged for operators who want tighter behavior.

## [2.6.42] - 2026-05-01

### Fixed

- **Integrity scan hung at "5000 active" forever when Celery workers died mid-scan**. The producer loop in `_run_file_changes_check` had no per-task timeout, so any `calculate_file_hash_task` whose worker disappeared remained `PENDING` in Redis indefinitely. `safe_task_ready()` was deliberately built to return `False` on Redis errors, which kept stuck tasks in `active_tasks` forever; combined with `MAX_CONCURRENT_SMALL=5000`, that pinned the producer at the cap with no submissions or completions. Each entry in `active_tasks` now carries a `submitted_at` monotonic timestamp; tasks older than `INTEGRITY_TASK_TIMEOUT_SECS` (default `1800`, env-overridable) are revoked, logged, and dropped from the active set.
- **Worker post-fork engine disposal could fail silently (v2.6.41 regression)**. `_setup_worker_process` now logs entry/exit and wraps `db.engine.dispose()` in a try/except, so a fork-time failure no longer leaves a child unable to log task activity. After the next deploy, the worker container should show `_setup_worker_process: starting in worker pid=...` and `_setup_worker_process: complete in worker pid=...` for each fork.
- **Misleading "queued" heartbeat label**. The integrity-scan heartbeat used to print `files_queued`, a cumulative counter that was only ever incremented, so an idle producer at the concurrency cap looked like a perpetually growing queue. Replaced with explicit `remaining` (unsubmitted files) and `abandoned` counts, e.g.: `Progress: 225881/1167919 processed, 5000 active, 937038 remaining, 0 abandoned`.
- **Integrity-scan UI froze at "75,000 of 1,167,919" while backend kept advancing**. Two related bugs: (1) the heartbeat block in `_run_file_changes_check` only wrote `last_heartbeat` to the `FileChangesState` row, leaving `phase_current`/`files_processed`/`progress_message` unchanged for multi-file Phase 2; (2) the periodic-update block used `total_files_processed % update_interval == 0`, which only matched when an outer-loop iteration captured `total_files_processed` *exactly* on a multiple of 100. Once the producer hit steady state at 5000 active tasks, each iteration completed thousands of tasks at once and the modulo check almost never aligned, so the row stayed at the value of the last lucky alignment. Replaced the modulo with a delta check (`total_files_processed - last_progress_update >= update_interval`) and made the heartbeat (now 10 s) write `files_processed`, `phase_current`, `phase_total`, and `progress_message` unconditionally.
- **Add Redis-backed real-time progress for the integrity scan**. v2.5.67 introduced this for the regular scan (`/api/scan-status` reads `get_scan_progress_redis()` when active) but the integrity-scan path was never updated. Added `get_file_changes_progress_redis` / `update_file_changes_progress_redis` / `clear_file_changes_progress_redis` in `pixelprobe/progress_utils.py` (separate `file_changes_progress:<check_id>` key namespace), wired the producer loop to write on every heartbeat and periodic-update tick, and made `/api/file-changes-status` prefer Redis values when the scan is active. The Redis key is cleared when the scan transitions to `complete` or `cancelled`.

## [2.6.41] - 2026-04-26

### Security

- **Bump `python-dotenv` from 1.0.0 to 1.2.2** to resolve CVE-2026-28684 (arbitrary file overwrite via symlink follow). MEDIUM severity. The pinned 1.0.0 had no fix available; 1.2.2 is the first release with the patch.

### Fixed

- **Single-file rescan UI flips to "done" then resumes minutes later**. The Flask route created a `ScanState` row keyed on its generated `scan_id`, then the Celery worker's `scan_service.scan_single_file` created a *second* `ScanState` with a different `scan_id` and tracked progress on that one. The UI's progress monitor lost track between the two rows and reported the scan complete before the worker had even started hashing the file. `scan_single_file` now accepts an optional `scan_id` and reuses the existing row when one matches; the Celery `scan_media_task` passes `scan_id` through for `scan_type='single'`.
- **Silent first-attempt failures on Celery worker scans** caused by post-fork PostgreSQL connection sharing. Symptom in logs: `psycopg2.DatabaseError: error with status PGRES_TUPLES_OK and no message from the libpq` on one worker, surfacing as a bare `NotImplementedError` from `sqlalchemy/engine/result.py:_indexes_for_keys` in a sibling worker. Fixed by disposing the SQLAlchemy engine in the `worker_process_init` Celery signal so each forked child builds its own connection pool. The existing log-handler setup in that signal has been merged into a single `_setup_worker_process` handler.
- **Scan progress flickers to "failed" during transient Celery retries**. `scan_media_task`'s catch-all exception handler used to set `phase='failed'` and `is_active=False` on every error, including ones that were about to be retried. The handler now keeps the row active (`phase='initializing'` with a "Retrying after error" progress message) when more retries remain, and only marks the scan failed once the retry budget is exhausted or the error is the known-fatal `PGRES_TUPLES_OK` connection-corruption case.

## [2.6.40] - 2026-04-20

### Security

- **Resolve open CodeQL and Dependabot findings**. No external behavior changes; exception details that previously leaked into HTTP response bodies are now logged server-side only.
  - **Stack trace exposure (49 sites)**: Exception detail (`str(e)`, `traceback`, `details`) no longer appears in API error responses. The `handle_errors` decorator and every route-level `except` across the API blueprints now returns a generic error message; the exception is logged server-side with `exc_info=True` for operators.
  - **Path injection (7 sites)**: `validate_file_path` and `validate_directory_path` resolve symlinks via `os.path.realpath` and validate with `os.path.commonpath` against the configured allowlist, defeating symlink-based escapes. **Behavior change**: `validate_directory_path` now enforces the configured scan-path allowlist by default; `POST /api/scan`, `POST /api/scan-files-parallel`, and `POST /api/parallel/scan` will reject directories outside `SCAN_PATHS` or the active `ScanConfiguration` entries (previously only `..` / `~` tokens were rejected). `POST /api/configurations` is exempt because it is defining a new allowlist entry. The unused `validate_path_exists` decorator was removed.
  - **Clear-text logging of sensitive data (3 sites)**: `tools/migrate_to_postgres.py` no longer holds the DB password in the `pg_config` dict while logging connection details; the password is stored in a dedicated parameter. Trusted-host config logging in `security.py` demoted from `info` to `debug`.
  - **GitHub Actions workflow permissions**: `.github/workflows/test.yml` declares `permissions: contents: read` at the workflow level.
  - **Reflective XSS (17 alerts)**: Reviewed; every flagged site returns JSON via `jsonify()` with no HTML sink. Dismissed as false positives; no code change required.

### Changed

- **Dependency bumps covering 6 CVEs**:
  - `Pillow` 12.1.1 -> 12.2.0 (FITS GZIP decompression bomb).
  - `requests` 2.32.5 -> 2.33.0 (`extract_zipped_paths` tempfile reuse).
  - `Flask-CORS` 5.0.1 -> 6.0.0 (path-matching CVEs; verified wildcard config is unaffected by the v6 specificity and case-sensitivity changes).
  - `pytest` 8.3.5 -> 9.0.3 (tmpdir handling CVE).
  - `black` removed from `requirements-test.txt`; it was unused dev tooling (no CI gate, no `pyproject.toml`, no pre-commit hook).

---

## [2.6.39] - 2026-04-20

### Fixed

- **Scheduled scans silently dropped when another scan is running**: When a scheduled scan's cron fire landed while a prior scan was still in progress, `MediaScheduler` logged `Scheduled scan {id} skipped - another scan is already running` and returned. APScheduler consumed the cron fire, so the next run advanced to the following cron interval (a weekly Sunday cleanup could go missing for an entire week). Added a queue-on-conflict retry that schedules a one-shot date-trigger retry `SCHEDULE_RETRY_DELAY_MINUTES` later (default 10), up to `SCHEDULE_RETRY_MAX_COUNT` times (default 6), and clears the retry state as soon as a scan actually starts. Applies to `_run_scheduled_scan`, `_run_periodic_scan`, and the env-var `_run_cleanup` HTTP 409 path. Retry state is kept in process memory; if the worker restarts while a retry is pending, the retry is lost and the schedule will fire again on its next regular cron.
- **Database log handler stuck after a flush failure**: `DatabaseLogHandler._flush_batch` attempted `db.session.rollback()` inside a silent try/except; if the rollback itself failed (stale connection, broken pipe), the scoped session stayed in `InvalidRequestError: Please rollback() fully before proceeding` state for the life of the writer thread, producing a flood of `[log_handler] Failed to flush N log entries to DB` lines. Added `db.session.remove()` after the rollback so the next flush starts with a fresh scoped session, and rate-limited the stderr report to once per 60s so recurring failures still surface instead of being logged exactly once at startup. Also moved `import sys` to the top of the file.

---

## [2.6.38] - 2026-04-06

### Fixed

- **Fix newly discovered files marked completed without being scanned**: Parallel chunk scanning (v2.6.32+) passed the parent `PixelProbe` instance to all chunk worker threads. Each `PixelProbe` uses a `StaticPool` with a single DB connection -- when 3+ chunk threads shared it concurrently, `_save_to_cache()` writes were silently lost due to transaction interference. Files ended up with `scan_status='completed'` but `scan_date=NULL` and `scan_tool=NULL`. Fixed by creating a per-thread `PixelProbe` instance in each chunk worker, giving each its own isolated DB connection. The v2.6.36 raw SQL `UPDATE SET scan_status = 'completed'` fix masked the issue by marking unscanned files as done.

---

## [2.6.37] - 2026-04-06

### Fixed

- **Remove hardcoded 30-chunk limit on scan progress grid**: The scan-status API limited chunk results to 30, hiding all other chunks from the UI worker grid. Removed the `.limit(30)` so the grid shows all chunks for the current scan.

---

## [2.6.36] - 2026-04-06

### Fixed

- **Fix scans completing with ~65% of files still pending**: `checker.scan_file()` committed `scan_status='completed'` via PixelProbe's separate StaticPool session (connection B), but batch queries used Flask's `db.session` (connection A) which never saw the change. Added raw SQL `UPDATE scan_results SET scan_status = 'completed'` via Flask's `db.session` after each successful scan, ensuring the next batch query on the same connection sees the status change. Previous fixes (`offset(0)` in v2.6.34, `expire_all()` in v2.6.35) failed because they couldn't bridge the cross-connection visibility gap.

---

## [2.6.35] - 2026-04-05

### Fixed

- **Fix scan leaving ~65% of files stuck in pending**: `_scan_chunk_files()` batch queries used Flask's `db.session` but `checker.scan_file()` committed `scan_status='completed'` via a separate PixelProbe session. SQLAlchemy's identity map in `db.session` retained stale ScanResult objects from previous batches, causing the `offset(0)` batch query to return the same files repeatedly instead of the next pending batch. Added `db.session.expire_all()` before each batch query to force fresh reads from PostgreSQL.

---

## [2.6.34] - 2026-04-05

### Fixed

- **Fix scan skipping ~25% of pending files per chunk**: Batch pagination within `_scan_chunk_files()` used `offset(batch_offset)` on queries filtered by `scan_status == 'pending'`. As each batch was scanned, files changed from 'pending' to 'completed', shrinking the result set. The incrementing offset then skipped files that shifted to lower positions. Fixed by using `offset(0)` for all pending-file queries since scanned files drop out of the result set automatically.

---

## [2.6.33] - 2026-04-05

### Fixed

- **Fix scan startup crash from dead `offset` variable**: `_create_scanning_chunks()` crashed every scan with `UnboundLocalError: cannot access local variable 'offset'`. Leftover from the offset-to-keyset pagination migration -- the function uses `last_path` to advance through results, but `offset += chunk_size` was never removed.
- **Fix IndexError on scan_state and scan_chunks ORM queries**: `db.create_all()` creates new tables but does not add columns to existing ones. Columns added to models (`num_workers`, `files_added`, `files_updated` on scan_state; `files_processed`, `is_complete`, `celery_task_id`, `files_added` on scan_chunks) were missing from the production database, causing `IndexError: tuple index out of range` when SQLAlchemy tried to load rows with fewer columns than the mapper expected. Added startup migration to sync missing columns.

---

## [2.6.32] - 2026-04-05

### Added

- **Per-worker scan progress grid**: Collapsible grid below the scan progress bar showing each parallel chunk worker's status, directory path, files scanned/total, and mini progress bar. Collapsed by default, expandable via toggle. Shows active, completed, and errored chunks with real-time updates. Responsive mobile layout hides progress bars and truncates paths.
- **Video freeze detection**: Detect videos with frozen frames (stuck picture while audio continues) using FFmpeg's `freezedetect` filter. Freeze events are reported as **warnings** (not corruption) with details including event count, total frozen time, percentage of video frozen, and per-event timestamps. Uses `-60dB` noise tolerance and `5s` minimum freeze duration with black frame filtering to reduce false positives. Configurable via `FREEZE_DETECTION_ENABLED` environment variable (default: `true`).
- **JPEG pixel corruption detection**: Detect visually corrupted JPEG files that pass PIL verification and ImageMagick validation but contain visible garbage (rainbow bands, solid color fill) in decoded pixel data. Uses row-averaged RGB sampling with two signals: sustained chaos (8+ consecutive rows with inter-row color difference > 100, catching rainbow garbage) and bottom-anchored solid fill (30+ consecutive identical rows extending to the image bottom, catching decoder fill). Designed to avoid false positives on high-contrast images like YouTube thumbnails.

### Fixed

- **Fix JPEG pixel analysis guards**: Added file size guard (skip > 10MB), image dimensions guard (skip > 30MP), 30s timeout, and downscale to ~200px wide before analysis (90KB vs 36MB full-resolution tobytes).
- **Fix parallel chunk session error**: `_parallel_scan_chunks` worker threads sharing Flask's scoped `db.session` caused "concurrent operations are not permitted" (isce) errors. Every chunk immediately errored, making the scan complete with 0 files processed. Fixed by calling `db.session.remove()` at thread start to force a fresh session per thread.
- **Fix stuck scan false-positive during long video processing**: The `db.session.expire(scan_state)` fix for the row-lock convoy also stopped `last_update` from being written. The UI progress worker that should maintain `last_update` independently fails to launch when Redis is congested from previous crash retries. Added raw SQL `last_update` refresh at chunk completion to maintain scan self-sufficiency.
- **Fix scan deadlock from concurrent progress updates**: 20 ThreadPoolExecutor worker threads all executed `UPDATE scan_state SET files_processed = files_processed + 1` on the same row simultaneously, creating a PostgreSQL row-level lock convoy that permanently blocked all scanning. Moved progress updates out of the per-future loop to a single batch-level update from the main thread after all futures complete.
- **Fix scan worker death on 500K+ pending files**: `_create_scanning_chunks()` loaded all file paths into memory at once via `.all()`, consuming ~200MB+ for 600K files. Replaced with `yield_per()` streaming that holds only one chunk (~1000 paths) in memory at a time. Added `cancel_futures=True` to ThreadPoolExecutor shutdown to prevent indefinite hangs. Added composite index `(scan_status, file_path)` for optimal query performance on pending file lookups.
- **Fix "0 of 0 files" progress display during scanning phase**: The scan status API returned `estimated_total=0` during the gap between phase transition and chunk counting. Added explicit Redis update after DB commit and a fallback to `phase_total` in the API when `estimated_total` is 0.
- **Filter black frame false positives from freeze detection**: Freeze events that overlap with black sections (scene transitions, studio logos, end credits) are now filtered out. Uses FFmpeg `blackdetect` filter chained with `freezedetect` in a single decode pass.
- **Fix scan stuck as active after completion**: Two issues -- (1) `_mark_scan_completed()` used raw SQL UPDATE but the ORM identity map still held stale `is_active=True`; when `_create_scan_report()` committed, it overwrote the completed state. Added `db.session.expire_all()` after the raw SQL commit. (2) Recovery endpoint and new scans didn't clear stale Redis progress keys, causing the API to return contradictory state (running + completed). Added `clear_scan_progress_redis()` to recovery and scan start.

---

## [2.6.2] - 2026-03-20

### Fixed

- **Fix `app_configs` seed data failure on startup**: Migration v2.6.0 INSERT failed with `NotNullViolation` on `created_at` column because SQLAlchemy's `create_all()` created the table without PostgreSQL `DEFAULT` clauses on timestamp columns. Added `server_default=func.now()` to AppConfig model and `ALTER TABLE ... SET DEFAULT` in migration to fix both new and existing deployments.
- **Fix log download 500 error**: The `/api/logs/download` streaming generator lost Flask app context, causing an `Internal Server Error`. Wrapped with `stream_with_context` to preserve the request context during streaming.
- **Add rate limit to `/api/logs/runs`**: Added missing `30 per minute` rate limit, consistent with other log endpoints.
- **Restrict purge endpoint filter scope**: Purge now only accepts its documented filters (`scan_id`, `before`, `level`), ignoring undocumented keys like `search` or `start_time` that were previously passed through.
- **Fix scan logs missing from Job Run dropdown**: The `DatabaseLogHandler` set up in the parent process had a dead `_writer_thread` in forked Celery worker children (threads don't survive fork). Added `worker_process_init` signal handler in `celery_config.py` to create a fresh handler in each worker child process.
- **Add test coverage for View Logs feature**: 47 new tests covering LogEntry/AppConfig models, log context vars, DatabaseLogHandler, all log API endpoints, path filter, and log cleanup.

---

## [2.6.0] - 2026-03-20

### Added

- **View Logs page** (#43): New "View Logs" page in the System section with persistent log storage in PostgreSQL, live log viewing with 3-second polling, filtering by level/time range/job run/search, traceback expand/collapse, and log download as `.log` file
- **Path filter on scan results** (#42): New dropdown filter on the scan results page to view results for a single configured scan path, populated from active SCAN_PATHS
- **LogEntry model**: New database table for persistent log storage with scan_id tagging via Python contextvars
- **AppConfig model**: New database table for application-level configuration (log retention, excluded loggers)
- **DatabaseLogHandler**: Background-threaded logging handler that batch-inserts log records to PostgreSQL without blocking app code (10k queue, 100-record batches)
- **Log context tagging**: Celery scan tasks automatically tag all log entries with scan_id and celery_task_id for filtering
- **Log retention cleanup**: Configurable retention period (default 30 days) with daily automated cleanup via scheduler
- **Log API endpoints**: GET /api/logs (filtered/paginated), GET /api/logs/runs, GET /api/logs/download, GET/PUT /api/logs/retention, POST /api/logs/purge
- **Path filter API**: GET /api/scan-paths returns active configured paths; GET /api/scan-results accepts `path` query parameter

---

## [2.5.70] - 2026-03-14

### Fixed

- SQLAlchemy SAWarning during chunk scanning caused by autoflush when accessing chunk attributes after commit

## [2.5.69] - 2026-03-14

### Security

- **Pillow 10.0.0 -> 12.1.1**: CVE-2023-50447 (CRITICAL 9.8, arbitrary code execution), CVE-2024-28219 (HIGH)
- **Jinja2 (transitive via Flask 3.1.3)**: CVE-2025-27516 (CRITICAL, sandbox breakout)
- **Werkzeug 2.3.7 -> 3.1.6**: CVE-2024-34069 (HIGH, RCE via debugger), CVE-2023-46136 (HIGH, DoS)
- **gunicorn 21.2.0 -> 23.0.0**: CVE-2024-1135, CVE-2024-6827 (HIGH 7.5, HTTP request smuggling)
- **requests 2.31.0 -> 2.32.5**: CVE-2024-35195 (MEDIUM, TLS bypass), CVE-2024-47081 (MEDIUM, credential leak)

### Fixed

- **RequestsDependencyWarning on worker startup**: Removed system `python3-chardet` 7.1.0 from Docker image that conflicted with requests' version check (fires on every gunicorn/celery worker start)
- **Node.js 20 deprecation warnings in CI**: Updated `actions/checkout` v3->v4, `actions/setup-python` v4->v5, `codecov/codecov-action` v3->v5

### Changed

- **Flask 2.3.3 -> 3.1.3**: Major upgrade required for Werkzeug 3.x compatibility
- **Werkzeug safe_join import**: Moved from `werkzeug.security` to `werkzeug.utils` (Werkzeug 3.x)
- **Pillow transpose API**: Updated `Image.FLIP_LEFT_RIGHT` to `Image.Transpose.FLIP_LEFT_RIGHT` (Pillow 12 deprecation)
- **Replaced all `Model.query.get(id)` with `db.session.get(Model, id)`**: 43 occurrences across 12 files -- `query.get()` is deprecated in SQLAlchemy 2.x / Flask-SQLAlchemy 3.x
- **Upgraded Flask ecosystem**: Flask-SQLAlchemy 3.1.1, Flask-CORS 5.0.1, Flask-Limiter 3.9.0, Flask-WTF 1.2.2, flask-restx 1.3.2
- **Upgraded supporting packages**: pillow-heif 0.22.0, SQLAlchemy 2.0.41, psycopg2-binary 2.9.10, celery 5.4.0, redis 5.2.1, reportlab 4.2.5, PyYAML 6.0.2, bcrypt 4.3.0, APScheduler 3.11.0
- **Upgraded test dependencies**: pytest 8.3.5, pytest-cov 6.1.1, types-requests 2.32.x
- Updated docker-compose.yml image tags to 2.5.69
- Updated CI test matrix from Python 3.9/3.10/3.11 to 3.10/3.11/3.12 (Pillow 12 requires Python >= 3.10; Python 3.9 is EOL)

---

## [2.5.67] - 2026-03-05

### Fixed

- **Fix stuck scan bug**: Scans could get permanently stuck as "active" after a Celery task crash (e.g., `psycopg2.DatabaseError`). The crash recovery handler in `scan_service.py` attempted to mark the scan as crashed but failed because the DB session was in a rolled-back state. Added `db.session.rollback()` before recovery writes and re-query the scan state with a fresh session.
- **Fix stuck scan detection for lost Celery tasks**: When Celery task state is `None` (task lost/unreachable), the `is_scan_running()` check previously assumed the scan was still running indefinitely. Now falls through to time-based detection -- if no update for over 1 hour with unknown task state, marks as crashed.
- **Fix scheduler stuck scan checker**: The `_check_stuck_scans` scheduler job now also verifies Celery task state. If a Celery task is gone AND no progress update for 5+ minutes, the scan is marked as crashed (previously only relied on 30-minute time threshold).
- **Fix Phase 3 progress display appearing frozen**: The scan-status API endpoint now reads real-time progress from Redis (instead of only PostgreSQL) when a scan is active. Redis is updated by the Celery scan worker on every file, while PostgreSQL lagged behind, causing the UI to show stale values like "97/397" or appear stuck.
- **Fix final progress not reflected in DB on scan completion**: All scan completion paths now write `files_processed` and `estimated_total` in the same SQL UPDATE that marks the scan as completed. Previously, the completion UPDATE only set `phase` and `is_active`, leaving stale progress values in PostgreSQL.
- **Fix UI worker exiting without final sync**: The `ui_progress_update_task` now performs a final Redis-to-PostgreSQL sync of progress values before exiting when it detects the scan is complete or inactive.
- **Fix ORM staleness in final Redis-to-DB sync**: Added `ui_session.refresh(scan_state)` before comparing Redis vs DB progress values in `_final_sync_redis_to_db()`. After `_mark_scan_completed()` writes via raw SQL, the ORM object could have stale values, causing unnecessary or missed updates.
- **Fix trailing whitespace in scan_routes.py**: Removed trailing whitespace on blank line.
- **Move inline import to module level in tasks.py**: `get_scan_progress_redis` was imported inline at line 775 despite being available from the existing module-level import.
- Files affected: `pixelprobe/services/scan_service.py`, `pixelprobe/api/scan_routes.py`, `pixelprobe/scheduler.py`, `pixelprobe/tasks.py`, `pixelprobe/progress_utils.py`

### Added

- **`get_scan_progress_redis()` in `pixelprobe/progress_utils.py`**: Read function for fetching real-time scan progress from Redis, used by the scan-status API endpoint.
- **`_mark_scan_completed()` in `pixelprobe/services/scan_service.py`**: Extracted helper that consolidates all scan completion SQL into a single method, replacing 7 duplicated SQL blocks.
- **Unit tests for new functions**: Added `tests/unit/test_progress_utils.py` with 12 tests covering `get_scan_progress_redis()`, `_final_sync_redis_to_db()` sync logic, and `_mark_scan_completed()` SQL behavior.

### Changed

- **Move all application modules into `pixelprobe/` package**: Moved `models.py`, `auth.py`, `config.py`, `media_checker.py`, `scheduler.py`, `celery_config.py`, `version.py` from root into the `pixelprobe/` package. Root now only contains entry points (`app.py`, `celery_worker.py`) and build/config files. Updated 73+ import statements across 43 files.
- **Move root `utils.py` into `pixelprobe/utils/helpers.py`**: Consolidated shared utilities (`ProgressTracker`, `create_state_dict`, `batch_process`, etc.) into the package.
- **Documentation cleanup**: Fixed port 5001 references (should be 5000), updated stale version references (v2.4.48/v2.4.93), fixed SQLite references (PostgreSQL-only since v2.2.0), rewrote PROJECT_STRUCTURE.md, fixed container name references.
- **Repository cleanup**: Removed legacy files (`database_migrations.py`, `init_db.py`, `operation_handlers.py`, `utils.py`, `migrations/` directory, broken shell scripts, obsolete patches, outdated development docs).
- Updated docker-compose.yml image tags from `pixelprobe:test-v2.4.93` to `ttlequals0/pixelprobe:2.5.67`.
- Updated `.env.example` with correct PostgreSQL defaults and added missing env vars.

---

## [2.5.66] - 2026-02-23

### Fixed

- **Healthcheck pings blocked by SSRF protection**: Since v2.5.64, healthcheck pings for scheduled scans could fail when the healthcheck server resolves to a private IP address. Added `TRUSTED_INTERNAL_HOSTS` environment variable that lets admins allowlist hostnames and/or CIDR ranges that should bypass SSRF private-IP blocking. Accepts comma-separated values (e.g., `myhost.local,192.168.5.0/24`). SSRF protection remains fully active for non-trusted hosts.
- Files affected: `pixelprobe/utils/security.py`, `config.py`, `version.py`, `docker-compose.yml`, `docs/CONFIGURATION.md`, `tests/test_security_fixes.py`

---

## [2.5.65] - 2026-02-20

### Fixed

- **Share INTERNAL_API_SECRET via Redis across gunicorn workers**: The auto-generated secret was unique per gunicorn worker, causing scheduler internal requests to fail with 401 when routed to a different worker. Now uses Redis `SETNX` to generate the secret once and share it across all workers and the celery container.
- Files affected: `app.py`, `version.py`

---

## [2.5.64] - 2026-02-19

### Security

- **Fix authentication bypass via X-Internal-Request header**: Replace trivially spoofable static string check (`X-Internal-Request: scheduler`) with cryptographic HMAC validation using a shared secret (`X-Internal-Secret`). Secret is auto-generated at startup if not set via `INTERNAL_API_SECRET` environment variable.
- **Fix SSRF in healthcheck and notification services**: Add `validate_safe_url()` function that resolves hostnames and blocks requests to private/reserved IP ranges (RFC 1918, link-local, loopback, cloud metadata 169.254.x.x). Applied to healthcheck pings, webhook notifications, and ntfy notifications.
- **Add redirect-safe HTTP session**: New `create_safe_session()` creates a `requests.Session` with a response hook that validates redirect `Location` headers against the same private IP blocklist, preventing SSRF via DNS rebinding or redirect chains.
- **Fix ntfy config field name mismatch**: `_validate_provider_config()` now accepts both `server_url` (canonical, matching notification_service.py) and `server` (legacy) field names for backward compatibility.
- Files affected: `auth.py`, `config.py`, `app.py`, `scheduler.py`, `pixelprobe/utils/security.py`, `pixelprobe/services/healthcheck_service.py`, `pixelprobe/services/notification_service.py`, `pixelprobe/api/notification_routes.py`, `version.py`

---

## [2.5.63] - 2026-02-18

### Code Simplification

- **Deduplicate `convert_to_tz()` in models.py**: Extract 8 identical inline copies into a single module-level function
- **Consolidate `rate_limit()` / `exempt_from_rate_limit()`**: Update canonical `pixelprobe/utils/rate_limiting.py` to match inline implementation, replace 5 inline copies in route files with imports
- **Deduplicate Bearer token extraction in auth.py**: Extract `_extract_bearer_token()` helper, replace 4 inline token-parsing blocks, remove dead `token = token` no-ops
- **Unify file extension lists**: Remove divergent extension sets from `pixelprobe/utils/helpers.py`, import canonical lists from `pixelprobe/constants.py`
- **Rename ambiguous `validate_file_path`**: Rename `validators.py` version to `validate_file_path_format()` to distinguish from security.py's path traversal prevention
- **Deduplicate `check_celery_available()`**: Move to new `pixelprobe/utils/celery_utils.py`, replace copies in `scan_routes.py` and `scan_routes_parallel.py`
- **Deduplicate `ContextTask` in celery_config.py**: Extract `_make_context_task()` factory, replace 3 identical inner class definitions
- **Replace deprecated `datetime.utcnow()`**: Use `datetime.now(timezone.utc)` in `models.py` (4 locations) and `security.py` (2 locations)
- **Extract `get_configured_scan_paths()`**: Create shared helper in `pixelprobe/utils/helpers.py`, replace 6+ inline DB-fallback-to-env patterns
- **Use `TERMINAL_SCAN_PHASES` constant**: Replace 5 hardcoded `['idle', 'completed', 'error', 'crashed', 'cancelled']` lists with import from `pixelprobe/constants.py`
- **Consolidate scheduler scan methods**: Extract `_filter_excluded_paths()` and `_execute_scan_request()` helpers in `scheduler.py`
- **Remove dead `truncate_scan_output()`**: Remove no-op function from `media_checker.py`, replace 7 call sites with direct variable reference
- **Merge `load_exclusions_with_patterns()`**: Read `exclusions.json` once instead of twice
- **Remove unnecessary `getattr()` calls**: Use direct attribute access in `ScanReport.to_dict()`
- **Break up `app.py`** (1137 -> 541 lines):
  - Extract migration functions to `pixelprobe/migrations/startup.py`
  - Extract scheduler lock management to `pixelprobe/scheduler_lock.py`
  - Extract startup cleanup routines to `pixelprobe/startup.py`
  - Simplify v2.2.68 column migration with loop
- Files affected: `models.py`, `auth.py`, `app.py`, `scheduler.py`, `celery_config.py`, `media_checker.py`, `version.py`, `pixelprobe/utils/rate_limiting.py`, `pixelprobe/utils/helpers.py`, `pixelprobe/utils/validators.py`, `pixelprobe/utils/celery_utils.py` (new), `pixelprobe/utils/__init__.py`, `pixelprobe/constants.py`, `pixelprobe/migrations/startup.py` (new), `pixelprobe/scheduler_lock.py` (new), `pixelprobe/startup.py` (new), `pixelprobe/api/admin_routes.py`, `pixelprobe/api/scan_routes.py`, `pixelprobe/api/scan_routes_parallel.py`, `pixelprobe/api/maintenance_routes.py`, `pixelprobe/api/stats_routes.py`, `pixelprobe/services/stats_service.py`

---

## [2.5.63] - 2026-02-18

### Code Simplification

- **Deduplicate `convert_to_tz()` in models.py**: Extracted 8 identical inline copies into a single module-level function
- **Consolidate `rate_limit()` / `exempt_from_rate_limit()`**: Updated canonical `pixelprobe/utils/rate_limiting.py` to match inline implementations and replaced all 4 inline copies in route files
- **Deduplicate Bearer token extraction in auth.py**: Extracted `_extract_bearer_token()` helper, replaced 4 inline copies, removed `token = token` no-ops
- **Unify file extension lists**: Removed divergent inline sets from `helpers.py`, now imports from `pixelprobe/constants.py`
- **Break up `app.py`**: Extracted 600+ lines into focused modules:
  - `pixelprobe/migrations/startup.py` -- all DB migration functions
  - `pixelprobe/scheduler_lock.py` -- Redis distributed lock management
  - `pixelprobe/startup.py` -- startup cleanup routines
  - Reduced `app.py` from 1137 to 541 lines
- **Deduplicate `check_celery_available()`**: Moved to `pixelprobe/utils/celery_utils.py`, replaced copies in `scan_routes.py` and `scan_routes_parallel.py`
- **Deduplicate `ContextTask` in `celery_config.py`**: Extracted `_make_context_task()` factory, replaced 3 identical class definitions
- **Replace deprecated `datetime.utcnow()`**: All occurrences in `models.py` and `security.py` now use `datetime.now(timezone.utc)`
- **Extract `get_configured_scan_paths()`**: Created shared helper in `pixelprobe/utils/helpers.py`, replaced 6+ inline "read from DB, fallback to env var" patterns
- **Use `TERMINAL_SCAN_PHASES` constant**: Replaced 5 hardcoded `['idle', 'completed', 'error', 'crashed', 'cancelled']` lists with the constant from `pixelprobe/constants.py`
- **Consolidate scheduler scan methods**: Extracted `_filter_excluded_paths()` and `_execute_scan_request()` helpers, reducing duplication between `_run_periodic_scan` and `_run_scheduled_scan`
- **Rename ambiguous `validate_file_path`**: Renamed validators.py version to `validate_file_path_format()` to avoid confusion with the security.py version
- **Remove dead `truncate_scan_output()`**: Was a no-op (returned input unchanged), replaced 7 call sites with direct variable usage
- **Merge `load_exclusions_with_patterns()`**: Now reads `exclusions.json` once instead of twice
- **Remove unnecessary `getattr()` calls**: `ScanReport.to_dict()` now accesses `num_workers`, `files_added`, `files_updated` directly
- **Update stale `deep_scan` comment**: Replaced outdated version-specific comment with generic backward-compat note
- Files affected: `models.py`, `auth.py`, `app.py`, `scheduler.py`, `celery_config.py`, `media_checker.py`, `pixelprobe/utils/rate_limiting.py`, `pixelprobe/utils/helpers.py`, `pixelprobe/utils/validators.py`, `pixelprobe/utils/celery_utils.py` (new), `pixelprobe/utils/security.py`, `pixelprobe/utils/__init__.py`, `pixelprobe/api/admin_routes.py`, `pixelprobe/api/scan_routes.py`, `pixelprobe/api/scan_routes_parallel.py`, `pixelprobe/api/maintenance_routes.py`, `pixelprobe/api/stats_routes.py`, `pixelprobe/services/stats_service.py`, `pixelprobe/migrations/startup.py` (new), `pixelprobe/scheduler_lock.py` (new), `pixelprobe/startup.py` (new)

---

## [2.5.62] - 2026-02-14

### Bug Fixes

- **Fix PostgreSQL CREATE INDEX race condition on container startup**: Replace `fcntl.flock` file lock with PostgreSQL advisory lock (`pg_advisory_lock`) for migration coordination
  - File locks only work within a single container's `/tmp` filesystem -- the app and celery-worker containers each have their own `/tmp`, so they raced against each other
  - Advisory locks work across ALL connections to the same PostgreSQL database, preventing the `duplicate key value violates unique constraint "pg_class_relname_nsp_index"` errors seen on every container restart (~8 errors per restart event)
  - Winner process acquires the lock and runs migrations; other processes block until complete then skip
  - Falls back to uncoordinated execution if advisory lock fails (each DDL statement already has its own idempotency handling via `IF NOT EXISTS` / `try/except`)
  - Extract `_run_all_migrations()` helper for cleaner code structure
  - Files affected: `app.py`

---

## [2.5.61] - 2026-02-02

### Bug Fixes

- **Fix file changes scan SQLAlchemy detached instance error**: Long-running integrity scans (20+ hours for 1M+ files) would crash at ~99.95% with "Instance has been deleted" errors
  - Root cause: ORM objects held in memory for the entire scan duration get expired by `db.session.commit()` calls
  - If concurrent jobs (e.g., "Check for stuck scans" every 5 minutes) delete database rows, subsequent attribute access on expired objects crashes
  - Solution: Load only needed columns and convert to plain Python dictionaries immediately after query
  - Dictionaries are immune to SQLAlchemy session expiration and concurrent database changes
  - Files affected: `pixelprobe/services/maintenance_service.py`

---

## [2.5.60] - 2026-02-01

### Cleanup

- **Repository slop cleanup**: Remove ~500+ lines of dead code and outdated documentation
  - Remove unused tool scripts: `fix_database_schema_v2.py`, `fix_hevc_warnings.py`, `fix_nal_unit_false_positives.py` (377 lines)
  - Remove unused utility functions: `handle_db_errors()` decorator and `update_state_progress()` helper from `utils.py`
  - Remove deprecated API endpoints: `/reset-stuck-scans` and `/recover-stuck-scan` (use `/scan/recovery` instead)
  - Remove orphaned Docker files: `docker-compose.simple.yml`, `docs/docker/Dockerfile.modern*`, `docs/docker/docker-compose.*.yml`
  - Remove all console.log/error/debug statements from JavaScript files (~78 statements)
  - Update documentation: Fix SQLite references to PostgreSQL (now required), remove dead documentation link
  - Delete `audit_reports/` directory - historical data preserved in CHANGELOG.md
  - Files affected: `utils.py`, `pixelprobe/api/scan_routes.py`, `static/js/app.js`, `static/js/state.js`, `static/js/auth.js`, various docs

---

## [2.5.59] - 2026-02-01

### Bug Fixes

- **Fix UI not showing scheduled scan progress**: Add background scan detection
  - Root cause: UI only checked for running scans on page load
  - If user opens page after a scheduled scan starts, no progress was shown
  - Solution: Added 30-second background interval to detect running scans
  - Automatically starts progress monitoring when a scan is detected
  - Affects all scan types: regular scans, cleanup, and integrity scans
  - Files affected: `static/js/app.js`

---

## [2.5.58] - 2026-01-29

### Bug Fixes

- **Fix duplicate scheduler pings (4x pings for scheduled tasks)**: Scheduler lock was being acquired by all 4 Gunicorn workers
  - Root cause: v2.5.11 fix assumed "same hostname = container restart" and force-acquired locks
  - Problem: With 4 Gunicorn workers in one container, ALL workers share the same hostname
    - Worker 1 acquires lock: `pixelprobe-app:pid1:timestamp`
    - Workers 2-4 see "same hostname" and FORCE ACQUIRE (treating it as stale self-lock)
    - Result: All 4 workers run schedulers, sending 4x pings for scheduled tasks
  - Solution: Check BOTH hostname AND PID when deciding whether to force-acquire
    - Same hostname + same PID = self-lock (refresh/re-acquire - OK)
    - Same hostname + different PID = sibling worker (only acquire if stale >65s)
    - Different hostname = remote container (only acquire if stale >65s)
  - Added helper functions for lock parsing and acquisition logic:
    - `parse_scheduler_lock()`: Parse lock value into (hostname, pid, timestamp)
    - `should_force_acquire_lock()`: Decision logic with clear reason codes
    - `start_scheduler_heartbeat()`: Consolidated heartbeat thread creation (DRY)
  - Added unit tests for all lock scenarios (7 test cases)
  - Files affected: `app.py`, `tests/test_scheduler.py`

---

## [2.5.57] - 2026-01-03

### Bug Fixes

- **Fix NameError crash on startup (regression from v2.5.56)**: App failed to start after scheduler fix
  - Root cause: v2.5.56 removed `is_celery_worker` definition but left a reference at line 1091
  - Error: `NameError: name 'is_celery_worker' is not defined`
  - Solution: Remove `is_celery_worker` from the file-lock fallback condition
  - The file-lock fallback (for local dev without Redis) now works for any process
  - Files affected: `app.py`

### Cleanup

- **Remove obsolete migration scripts**: Migrations now run automatically on startup
  - Deleted `tools/migrate_db.py` - v2.2.46/47 migrations now handled by app_startup_migration.py
  - Deleted `tools/migrate_db_safe.py` - lock handling integrated into automatic migrations
  - Deleted `tools/run_migration.py` - legacy wrapper no longer needed
  - Updated documentation to reflect automatic migration system
  - Files affected: `tools/README.md`, `tools/MIGRATION_GUIDE.md`, `docs/maintenance/TOOLS_AND_SCRIPTS.md`

---

## [2.5.56] - 2026-01-03

### Bug Fixes

- **Fix scheduler not executing jobs**: Revert to v2.5.19 scheduler behavior
  - Root cause: v2.5.33 added `is_celery_worker` check that forced scheduler to only run in celery-worker
  - Problem: Celery's prefork model is incompatible with APScheduler's BackgroundScheduler threads
    - Celery main process loads app and starts scheduler thread
    - Celery then forks worker processes
    - Threads don't survive fork - scheduler thread only exists in parent process
    - Parent process doesn't handle requests, Flask context is broken
    - Jobs are scheduled but never execute
  - Solution: Remove `is_celery_worker` check, allow any process to acquire scheduler lock
    - Gunicorn workers work correctly with APScheduler (each imports app independently)
    - Redis SETNX ensures only one scheduler runs at a time
  - Files affected: `app.py`

- **Fix integrity scan reports missing changed files**: Changed files list never populated
  - Root cause: Local `changed_files` variable was populated but never assigned to `self.changed_files_list`
  - The instance variable was initialized empty and never updated
  - Report generation uses `getattr(self, 'changed_files_list', [])` which always returned empty list
  - Solution: Add `self.changed_files_list = changed_files` after Phase 2a processing
  - Files affected: `pixelprobe/services/maintenance_service.py`

---

## [2.5.55] - 2026-01-02

### UI Improvements

- **Rename "File Changes" to "Integrity Scan"**: Consistent terminology throughout the UI
  - The feature was renamed several versions back but some user-facing text still used the old "File Changes" terminology
  - Updated scan reports filter dropdown from "File Changes" to "Integrity Scan"
  - Updated schedule type dropdowns from "Integrity Check" to "Integrity Scan"
  - Updated all notification messages:
    - "File Changes Check Progress" -> "Integrity Scan Progress"
    - "File changes check started..." -> "Integrity scan started..."
    - "File changes check completed!" -> "Integrity scan completed!"
    - "File changes check cancelled" -> "Integrity scan cancelled"
    - "File changes check cancellation requested" -> "Integrity scan cancellation requested"
    - "Checking for file changes..." -> "Checking file integrity..."
    - "No file changes detected" -> "No integrity issues detected"
  - Updated scan type display mapping from "Integrity Check" to "Integrity Scan"
  - Files affected:
    - `templates/index.html` - Dropdown option text (3 locations)
    - `static/js/app.js` - Notification messages and type mapping (8 locations)

---

## [2.5.54] - 2026-01-01

### Bug Fixes

- **Fix persistent Redis connection crashes in integrity scan**: Enhanced Celery task result handling
  - Root cause: v2.5.53 wrappers had insufficient retry logic (3 retries, 0.5s delay)
  - When Redis connection pool gets corrupted, ALL connections are bad
  - Hitting the same broken pool 3 times doesn't help
  - **Solution**: Enhanced safe wrappers with exponential backoff and connection pool reset
    - `safe_task_ready()`: Now 5 retries with exponential backoff (1s, 2s, 3s, 4s)
    - `safe_task_get()`: Now 5 retries with exponential backoff
    - Both now reset the connection pool on first failure to get fresh connections
    - Added `reset_redis_pool()` function to force-disconnect and recreate pool
  - **Additional fixes**:
    - Added `safe_check_task_state()` wrapper for scan_routes.py AsyncResult access
    - Protected all AsyncResult.state access patterns from Redis crashes
    - Added Celery transport options for connection stability:
      - `socket_keepalive: True` to detect dead connections
      - `health_check_interval: 60` for periodic connection validation
      - `socket_timeout: 30` and `socket_connect_timeout: 30`
      - Applied to both broker and result backend transport options
  - **Regression origin**: v2.4.60 introduced `redis.from_url()` which returns low-level Connection
    object instead of full Redis client. v2.5.51 fixed app-level Redis, v2.5.53 added Celery wrappers,
    but wrappers needed enhanced retry logic with pool reset.
  - Files affected:
    - `pixelprobe/progress_utils.py` - Added reset_redis_pool()
    - `pixelprobe/services/maintenance_service.py` - Enhanced safe_task_ready/safe_task_get
    - `pixelprobe/api/scan_routes.py` - Added safe_check_task_state wrapper
    - `celery_config.py` - Added transport options for resilience

---

## [2.5.53] - 2026-01-01

### Bug Fixes

- **Fix Celery task result Redis connection errors**: Integrity scan crashed when checking task results
  - Root cause: Celery's `task.ready()` and `task.get()` use their own internal Redis connection, separate from our fixed Redis client
  - When Celery's Redis connection gets reset, task result checking fails with `ConnectionResetError`
  - Error message: `'Connection' object has no attribute 'register_connect_callback'`
  - **Solution**: Added wrapper functions with retry logic for Celery task result operations
    - `safe_task_ready()`: Safely check task status with 3 retries and 0.5s delay
    - `safe_task_get()`: Safely get task result with 3 retries and 0.5s delay
    - Both functions catch `redis.ConnectionError`, `redis.TimeoutError`, `ConnectionResetError`, and `AttributeError`
    - On transient failure, retries before giving up
  - Files affected: `pixelprobe/services/maintenance_service.py`

---

## [2.5.52] - 2026-01-01

### Bug Fixes

- **Fix HEIC false positive corruption detection for iOS 18 files**: HEIC files from iOS 18 devices were incorrectly flagged as corrupted
  - Root cause: Older libheif versions (Ubuntu 24.04 ships 1.17.x) don't support iOS 18's use of shared auxiliary images
  - Error message: `"Too many auxiliary image references (2.0)"` from libheif/ImageMagick
  - This is a valid HEIF structure per spec, not file corruption (see: github.com/strukturag/libheif/issues/1190)
  - **Solution**: Detect libheif limitation errors and treat as warnings instead of corruption
    - Added detection for "auxiliary image" and "too many auxiliary" errors in ImageMagick stderr
    - Added detection for "cannot identify image file" errors on HEIC files in PIL
    - Files with this error now show as warning "HEIC validation skipped: libheif version limitation"
    - Files are no longer incorrectly marked as corrupted
  - Note: Full iOS 18 HEIC support requires libheif 1.19.8+ (not yet available in Ubuntu repos)
  - Files affected: `media_checker.py`

---

## [2.5.51] - 2026-01-01

### Bug Fixes

- **Fix Redis connection crash in maintenance service**: Application crashed with `ConnectionResetError`
  - Root cause: `redis.from_url()` returns low-level Connection object, not full Redis client
  - Error occurred when Redis connection was reset during file changes check
  - Error message: `'Connection' object has no attribute 'register_connect_callback'`
  - **Solution**: Created robust Redis connection handling with retry logic
    - Added connection pooling with health checks (`health_check_interval=30`)
    - Added automatic retry on connection failures (3 attempts with 1s delay)
    - Added `socket_keepalive=True` to detect stale connections
    - Added `retry_on_timeout=True` for transient failures
  - New utility functions in `progress_utils.py`:
    - `get_redis_client()`: Returns properly configured Redis client with connection pool
    - `get_redis_info()`: Safely gets Redis server info with retry logic
    - `with_redis_retry()`: Decorator for adding retry logic to Redis operations
  - Updated `maintenance_service.py` to use `get_redis_info()` instead of `redis.from_url()`
  - Updated `app.py` version endpoint to use `get_redis_info()` instead of `redis.from_url()`
  - Files affected: `pixelprobe/progress_utils.py`, `pixelprobe/services/maintenance_service.py`, `app.py`

---

## [2.5.50] - 2025-12-29

### Bug Fixes

- **Fix scheduler not starting after container restart**: Stale Redis lock prevented scheduler from starting
  - When container is killed with SIGKILL, Redis scheduler lock is not released
  - If container restarts within 60s TTL, it sees its own stale lock and skips starting scheduler
  - Added hostname to lock value format (`hostname:pid:timestamp`) to detect self-locks
  - Container restart now force-acquires its own stale lock immediately
  - Maintains backward compatibility with old lock format (`pid:timestamp`)
  - Files affected: `app.py`

---

## [2.5.49] - 2025-12-28

### Bug Fixes

- **Fix CSP blocking Chart.js source maps**: Added cdn.jsdelivr.net to connect-src directive
  - Browser was blocking requests to fetch Chart.js source maps from CDN
  - CSP connect-src only allowed 'self', now includes https://cdn.jsdelivr.net
  - Files affected: `app.py`

---

## [2.5.48] - 2025-12-28

### Bug Fixes

- **Fix Python 3.9 compatibility**: Type annotation `str | None` not supported in Python 3.9
  - GitHub Actions runs tests on Python 3.9, which doesn't support PEP 604 union types
  - Changed `-> str | None` to `-> Optional[str]` in notification_routes.py
  - Added `from typing import Optional` import
  - Files affected: `pixelprobe/api/notification_routes.py`

---

## [2.5.47] - 2025-12-28

### Bug Fixes

- **Widen scan_id columns to VARCHAR(64)**: Old queued tasks with 47-char scan_ids were stuck in retry
  - v2.5.46 fixed new scans but old tasks in Celery queue still had long scan_ids
  - Widened scan_id columns in scan_state, scan_chunks, cleanup_state from 36 to 64 chars
  - Added automatic database migration on startup to widen existing columns
  - This allows old stuck tasks to complete and unblock the scheduler
  - Files affected: `models.py`, `tools/app_startup_migration.py`

---

## [2.5.46] - 2025-12-28

### Bug Fixes

- **Fix scheduled scan failing with DataError**: scan_id exceeded VARCHAR(36) column limit
  - Scheduler adds timestamp: `scheduled_{id}_{YYYYMMDD_HHMMSS}` (28 chars)
  - scan_routes.py was adding ANOTHER timestamp, making it 47 chars
  - Database column `scan_state.scan_id` is VARCHAR(36)
  - **Solution**: Removed duplicate timestamp from scan_routes.py
  - Scheduler's timestamp is already unique per scheduled run
  - Files affected: `pixelprobe/api/scan_routes.py`

---

## [2.5.45] - 2025-12-28

### Bug Fixes

- **Fix v2.5.44 syntax error**: Fixed `nonlocal` statement that caused app crash
  - `nonlocal` only works for enclosing function scopes, not block-level scopes
  - Changed `scheduler_initialized` to use mutable list `[False]` pattern
  - Files affected: `app.py`

---

## [2.5.44] - 2025-12-28

### Bug Fixes

- **Fix scheduler not starting after container restart**: Scheduler lock has no retry mechanism
  - When container restarts quickly (~23 seconds), the old Redis lock hasn't expired yet
  - Lock check only happened ONCE at startup - no retry if acquisition failed
  - Old lock would expire at 60s, but new celery-worker never rechecked
  - Result: Scheduler never started, scheduled scans never ran
  - **Solution**: Added background retry thread that checks for lock every 30 seconds
  - Retries up to 10 times (~5 minutes) to acquire expired or stale locks
  - Once acquired, starts normal heartbeat thread to maintain the lock
  - Files affected: `app.py`

---

## [2.5.43] - 2025-12-28

### Bug Fixes

- **Fix startup race condition in sync_scan_paths_to_db()**: v2.5.41 crashed on startup
  - Multiple gunicorn workers starting simultaneously caused duplicate key violations
  - Changed from check-then-insert to PostgreSQL `INSERT ... ON CONFLICT DO NOTHING`
  - This is an atomic operation that safely handles concurrent inserts
  - Files affected: `app.py`

- **Deploy scheduler API hostname fix**: Scheduled scans now work from celery-worker
  - v2.5.42 fix for using `pixelprobe:5000` instead of `localhost:5000` is now deployed
  - Celery-worker can now reach the main app to trigger scans

---

## [2.5.42] - 2025-12-28

### Bug Fixes

- **Fix scheduler API calls from celery-worker**: Scheduler couldn't reach main app API
  - In Docker, celery-worker's `localhost:5000` doesn't reach the main pixelprobe container
  - Added `_get_api_base_url()` method that detects Docker environment
  - Uses `http://pixelprobe:5000` (Docker service name) instead of `localhost`
  - Supports `API_BASE_URL` env var override for custom configurations
  - Files affected: `scheduler.py`

---

## [2.5.41] - 2025-12-28

### Bug Fixes

- **Sync SCAN_PATHS to database**: Fixed scheduler not being able to read scan paths in celery-worker
  - Main app now syncs SCAN_PATHS from environment to `scan_configurations` database table on startup
  - Scheduler reads paths from database instead of environment variable
  - This allows celery-worker to access paths via shared database without needing env var
  - Fallback to env var if database is empty (backwards compatibility)
  - Files affected: `app.py`, `scheduler.py`

---

## [2.5.40] - 2025-12-28

### Bug Fixes

- **Missing SCAN_PATHS in celery-worker**: Fixed docker-compose.yml missing scan-related environment variables for celery-worker container
  - When scheduler was moved to celery-worker in v2.5.28, the SCAN_PATHS env var was never added
  - Added SCAN_PATHS, EXCLUDED_PATHS, EXCLUDED_EXTENSIONS, PERIODIC_SCAN_SCHEDULE, CLEANUP_SCHEDULE, and TZ to celery-worker
  - **IMPORTANT**: Users must also add SCAN_PATHS to their production celery-worker container (Portainer/Docker Compose)
  - Files affected: `docker-compose.yml`

---

## [2.5.39] - 2025-12-28

### Improvements

- **Stale Scheduler Lock Detection**: Added automatic takeover of stale scheduler locks
  - When celery-worker starts and lock acquisition fails, checks if lock timestamp is >65s old
  - If lock is stale (heartbeat stopped), forces acquisition and initializes scheduler
  - Prevents scheduler from being stuck when old worker dies without releasing lock
  - Logs lock age for debugging: "lock held by: X, age=Ys"
  - Files affected: `app.py`

---

## [2.5.38] - 2025-12-28

### Bug Fixes

- **Restore v2.5.19 Scheduled Scan Logic**: Fixed all regressions introduced in v2.5.32
  - Added empty SCAN_PATHS validation with proper error logging
  - Added empty filtered_paths validation after exclusion filtering
  - Restored timestamp in scan_id (`scheduled_{id}_{timestamp}`) to prevent false "already completed" detection
  - Updated schedule_id parsing for healthcheck to handle timestamp format
  - Root cause: v2.5.32 accidentally reverted fixes from v2.5.19 while trying to fix SQLAlchemy session issues
  - Files affected: `scheduler.py`

---

## [2.5.37] - 2025-12-28

### Improvements

- **SCAN_PATHS Fallback Debug Logging**: Added logging to diagnose why scheduled scans with empty paths aren't falling back to SCAN_PATHS env var
  - Logs the raw `scan_paths` value from database
  - Logs the SCAN_PATHS env var value when fallback is triggered
  - Files affected: `scheduler.py`

---

## [2.5.36] - 2025-12-28

### Improvements

- **Schedule Debug Logging**: Added detailed logging to show all schedules in database during scheduler startup
  - Logs total count of schedules in database
  - Lists each schedule with name, id, is_active status, and cron expression
  - Helps diagnose why specific schedules may not be loading
  - Files affected: `scheduler.py`

---

## [2.5.35] - 2025-12-28

### Bug Fixes

#### Fixed

- **Schedule Updates Not Reaching Celery Worker**: Fixed schedule changes in UI not being applied
  - Root cause: v2.5.33/34 moved scheduler to celery worker, but `scheduler.update_schedules()` was called from gunicorn (Flask) where scheduler doesn't run
  - The scheduler silently skipped updates since `scheduler.running = False` in gunicorn
  - Fix: Created `reload_schedules_task` Celery task to trigger schedule reload in the worker
  - Now when schedules are created/updated/deleted via UI, a Celery task notifies the worker to reload
  - Files affected: `pixelprobe/tasks.py`, `pixelprobe/api/admin_routes.py`

---

## [2.5.34] - 2025-12-28

### Bug Fixes

#### Fixed

- **HOTFIX: v2.5.33 Missing Import**: Fixed app crash on startup due to missing `import sys`
  - v2.5.33 added code using `sys.argv` but forgot to import the `sys` module
  - Files affected: `app.py`

---

## [2.5.33] - 2025-12-28

### Bug Fixes

#### Fixed

- **Scheduler Not Running After Container Restart**: Fixed scheduled scans not executing after container restart
  - Root cause: v2.5.28 removed unconditional Redis lock deletion, which unintentionally allowed gunicorn workers to acquire the scheduler lock before the Celery worker
  - Gunicorn's pre-fork model is incompatible with APScheduler's background threads
  - Fix: Only allow Celery worker to attempt scheduler lock acquisition
  - Added check for `celery` in `sys.argv[0]` to detect Celery worker context
  - Non-Celery processes (gunicorn workers) now skip scheduler initialization entirely
  - This restores all scheduled tasks: periodic scans, cleanup scans, stuck scan checker, and user-defined schedules
  - Files affected: `app.py`

---

## [2.5.32] - 2025-12-27

### Bug Fixes

#### Fixed

- **Scheduled Scans Running with Empty Paths**: Fixed scheduled scans not scanning any files after container restart
  - Root cause: SQLAlchemy session expiry after `db.session.commit()` in scheduler
  - After commits (healthcheck ping update, last_run update), the schedule object expires
  - When `schedule.scan_paths` was accessed after commit, lazy-loading failed in celery worker context
  - Fix: Cache `scan_paths`, `scan_type`, and `schedule.name` immediately after loading, before any commits
  - This issue manifested when the scheduler ran in the celery worker instead of the Flask app
  - Files affected: `scheduler.py`

---

## [2.5.31] - 2025-12-26

### Bug Fixes

#### Fixed

- **Pending Files Fix Incomplete in v2.5.30**: The retry mechanism was only added to 2 of 6 scan completion paths
  - Root cause: v2.5.30 added `_retry_pending_files()` to chunk-based scan paths but missed direct file scan paths
  - Fixed scan paths now include:
    - `_sequential_scan` - used for small file lists in sequential mode
    - `_parallel_scan` - used for small file lists in parallel mode
    - `_sequential_scan_selected_chunks` - used for selected file scans in sequential mode
    - `_parallel_scan_selected_chunks` - used for selected file scans in parallel mode
  - Added enhanced logging with scan method identifiers (e.g., "PARALLEL DIRECT", "SEQUENTIAL SELECTED CHUNKS")
  - Warning logged if any files remain pending after retries
  - Files affected: `pixelprobe/services/scan_service.py`

---

## [2.5.30] - 2025-12-24

### Bug Fixes

#### Fixed

- **Pending Files Not Processed During Scan**: Fixed scans completing with files left in 'pending' status
  - Root cause: Scan would iterate through files and mark as "complete" based on iteration count, not actual database saves
  - Added retry mechanism at scan completion to re-scan any files still in 'pending' status (up to 2 retries)
  - Ensures all files are processed before marking scan as complete
  - Added verification warning in logs if any files remain pending after retries
  - Affects both sequential and parallel scan completion paths
  - Files affected: `pixelprobe/services/scan_service.py`

- **Silent Database Save Failures**: Added tracking for failed database saves during scanning
  - Added `failed_saves` and `successful_saves` counters to PixelProbe class
  - Logs cumulative failed save count for debugging
  - Added `get_save_stats()` and `reset_save_stats()` methods for monitoring
  - Files affected: `media_checker.py`

#### Added

- Pending file count now included in scan report statistics query
- Warning logged when files remain in 'pending' status after scan completion

---

## [2.5.29] - 2025-12-21

### Bug Fixes

#### Fixed

- **Scan Chunk Pagination Race Condition**: Fixed files being skipped during scan due to pagination bug
  - Chunks were using OFFSET/LIMIT on pending files query, but as files were scanned their status changed
  - This caused the result set to shrink, making subsequent chunk offsets skip files
  - New approach: Capture file path boundaries at chunk creation time (JSON format: FCP)
  - Query by stable path range (`file_path >= first AND file_path <= last`) instead of offset/limit
  - Maintains backwards compatibility with legacy FILE_CHUNK format
  - Files affected: `pixelprobe/services/scan_service.py`

#### Updated Screenshots

- Updated `docs/screenshots/features/scan-schedules.png` with working Font Awesome icons

---

## [2.5.28] - 2025-12-21

### Bug Fixes

#### Fixed

- **Font Awesome Icons Missing**: Fixed CSP blocking Font Awesome CSS from cdnjs.cloudflare.com
  - Changed Font Awesome CDN from `cdnjs.cloudflare.com` to `cdn.jsdelivr.net` (already in CSP whitelist)
  - Affects both `templates/index.html` and `templates/login.html`
  - Icons now display correctly on all pages (mobile and desktop)

- **Scheduler Race Condition**: Fixed duplicate scheduler initialization during container startup
  - Removed unconditional Redis lock deletion in `app.py` that caused race condition
  - When both pixelprobe and celery-worker containers start simultaneously, they were deleting each other's locks
  - The 60-second TTL already handles stale locks from crashed containers
  - This fixes premature scan completion and pending files accumulation issues

---

## [2.5.27] - 2025-12-20

### Security Audit Gap Implementation

This release addresses gaps identified in the November 2025 security audit verification.

#### Security

- **BREAKING**: Removed API token query parameter support (P0 security fix)
  - Tokens in URLs are logged in server access logs and exposed in browser history
  - API tokens MUST now be sent via `Authorization: Bearer <token>` header only
  - Affects `auth_required()`, `check_auth()`, and `get_authenticated_user()` functions
- Added comprehensive security headers middleware (P1):
  - `X-Frame-Options: SAMEORIGIN` - Prevents clickjacking
  - `X-Content-Type-Options: nosniff` - Prevents MIME sniffing
  - `X-XSS-Protection: 1; mode=block` - Legacy XSS protection
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Content-Security-Policy` - CSP with 'unsafe-inline' for inline handlers
  - `Strict-Transport-Security` - HSTS for HTTPS connections
- Added session inactivity timeout (30 minutes) (P1)
  - Automatically logs out users after 30 minutes of inactivity
  - Returns 401 for API requests, redirects for UI requests

#### Performance

- Added composite database indexes for common query patterns (P1):
  - `idx_status_corrupted` on `(scan_status, is_corrupted)`
  - `idx_scan_date_corrupted` on `(scan_date DESC, is_corrupted)`
  - `idx_exists_status` on `(file_exists, scan_status)`
  - Indexes are created via startup migration with `IF NOT EXISTS`

#### Added

- Notification system API routes (P3 feature completion):
  - `GET/POST /api/notifications/providers` - List/create notification providers
  - `GET/PUT/DELETE /api/notifications/providers/<id>` - CRUD for providers
  - `POST /api/notifications/providers/<id>/test` - Test notification delivery
  - `GET/POST /api/notifications/rules` - List/create notification rules
  - `GET/PUT/DELETE /api/notifications/rules/<id>` - CRUD for rules
  - Supports Pushover, ntfy.sh, and webhook providers
- ARIA accessibility attributes (P3 compliance):
  - Navigation landmarks: `role="navigation"`, `role="main"`, `role="banner"`
  - Decorative icons: `aria-hidden="true"` on Font Awesome icons
  - Interactive elements: `aria-label` on buttons with icons only
  - Progress tracking: `role="progressbar"` with aria-value attributes
  - Live regions: `aria-live="polite"` for dynamic content updates

#### Context7 Validation

- Verified existing PIL/Pillow image validation follows best practices
- Verified existing FFmpeg video validation follows best practices
- Applied SQLAlchemy composite index patterns from Context7 docs
- Applied Flask-Session inactivity timeout patterns from Context7 docs

#### Testing

- Fixed flaky schedule reactivation test that failed due to APScheduler timing edge cases
- Updated pytest configuration to properly scope test discovery to `tests/` and `scripts/` directories
- UI screenshot updates with correct viewport sizing and element-specific captures

---

## [2.5.26] - 2025-12-16

### Fix: Cleanup/File Changes Duplicate Reports & Missing Healthcheck Ping

#### Problem 1: Duplicate Reports

Scheduled cleanup and file_changes scans were creating multiple reports:
- Gunicorn runs 4 workers, each with its own process-local `current_cleanup_thread` variable
- When scheduler triggered 4 simultaneous requests, 3 workers passed the "already running" check
- Result: 3 cleanup operations ran concurrently, creating 3 reports

#### Problem 2: Missing Finish Healthcheck Ping

Cleanup and file_changes scans sent start ping but no completion ping:
- Reports were created without `scan_id` field (needed `scheduled_X` format)
- `send_healthcheck_completion()` was never called after report creation
- Healthcheck monitoring showed scan started but never finished

#### Solution

**Duplicate Prevention:**
- Added database-level check using `CleanupState.is_active` and `FileChangesState.is_active`
- Database check provides cross-worker visibility (vs process-local thread check)

**Healthcheck Integration:**
- Scheduler now passes `schedule_id` in API request body
- API routes accept and pass `schedule_id` to async functions
- Report creation now sets `scan_id=scheduled_{id}` for schedule association
- Calls `MediaScheduler.send_healthcheck_completion()` after report creation

#### Files Modified

- `pixelprobe/api/maintenance_routes.py`: Database-level duplicate checks, schedule_id handling
- `pixelprobe/services/maintenance_service.py`: Schedule-aware report creation with healthcheck ping
- `scheduler.py`: Pass schedule_id to cleanup/file_changes API requests

---

## [2.5.25] - 2025-12-04

### Fix: Scheduled Scans Not Executing (Falsely Detected as Already Completed)

#### Problem

Scheduled scans were being skipped with message "Scan already completed (from previous attempt)":
- All scheduled scans used static scan_id like `scheduled_1` or `scheduled_periodic`
- Celery task checked if scan_id was already completed
- Found PREVIOUS day's completed scan and skipped the new scan
- Healthcheck received "success" ping despite no actual scanning

#### Root Cause

- `scheduler.py` sent `source: scheduled_1` to `/api/scan`
- `scan_routes.py` used this directly as `scan_id`
- `tasks.py` queried ScanState for `scan_id=scheduled_1` and found completed state
- Task returned early thinking it was a retry of a completed scan

#### Solution

- Added timestamp to scheduled scan_id: `scheduled_1_20241204_180000`
- Each scheduled scan now has a unique identifier
- Celery retry detection only matches same-session scans
- Applies to both `scheduled_{id}` and `scheduled_periodic` sources

#### Impact

- Scheduled scans now execute correctly
- New files are discovered on schedule
- Healthcheck pings reflect actual scan execution

---

## [2.5.24] - 2025-12-03

### Fix: Stage 3 FFmpeg Version Compatibility

#### Problem

v2.5.23's exit-code-only approach failed due to FFmpeg version differences:
- **FFmpeg 8.0 (macOS)**: Returns exit code 0 for PPS seek errors
- **FFmpeg 6.1.1 (Docker/Ubuntu 24.04)**: Returns non-zero exit code for same errors

#### Solution

Stage 3 (multi-point sampling) now **never marks files as corrupted**:
- All Stage 3 output is logged as debug info only
- Stage 1 (full decode from beginning, no seeking) remains the authoritative check

#### Impact

- 3 remaining false positives now correctly detected as healthy
- Works consistently across all FFmpeg versions
- No pattern matching or exit code dependencies

---

## [2.5.23] - 2025-12-03

### Fix: Remove Aggressive Error Detection from Stage 3 Multi-Point Sampling

#### Changed

- **Removed `-err_detect crccheck+bitstream` from Stage 3 multi-point sampling**
  - Aggressive error detection is incompatible with seeking mid-stream
  - HEVC decoder reports errors (PPS missing, first slice missing) until finding keyframe
  - These are normal seek artifacts, not corruption

- **Simplified Stage 3 logic**
  - Now just checks FFmpeg exit code (0 = success)
  - Removed complex benign pattern matching (was a maintenance burden)
  - Stage 4 still uses aggressive detection (starts from beginning, no seek issues)

#### Impact

- All false positives from HEVC seek artifacts eliminated
- Cleaner, more maintainable code
- Actual corruption still detected via non-zero exit code

---

## [2.5.22] - 2025-12-03

### Fix: Multi-Point Sampling False Positives (Stage 3)

#### Fixed

- **HEVC seek artifacts no longer trigger false corruption**
  - `First slice in a frame missing` - Normal HEVC seek behavior
  - `PPS id out of range` - HEVC initialization after seek

- **EOF/seek-related messages no longer trigger false corruption**
  - `Cannot determine format` - Video stream shorter than container
  - `Nothing was written into output` - No frames at seek position
  - `Error marking filters` / `Error while filtering` - Consequence of EOF
  - `Received no packets` - No data at seek position

- **Audio codec warnings no longer trigger false corruption**
  - TrueHD, DTS-HD warnings (audio issues, not video corruption)
  - `quant_step_size` TrueHD specific warning

#### Impact

- 19+ files previously marked as "Corrupted" now correctly show as "Healthy"
- Files affected: Station Eleven (all episodes), Law Abiding Citizen, Avengers Endgame, Mufasa, Lord of the Rings, etc.

#### Files Modified

- `media_checker.py` - Added benign patterns in `_check_multipoint_sampling()`

---

## [2.5.21] - 2025-12-03

### Fix: DTS/PTS Warnings Should NOT Mark Files as Warning Status

#### Fixed

- **Files with only DTS/PTS timestamp warnings now marked as HEALTHY (not Warning)**
  - Removed `warning_details` assignment in `_check_video_corruption()` for benign warnings
  - DTS/PTS warnings are common muxer artifacts in H.264/HEVC files
  - Files with NAL unit warnings or reference frame warnings also stay HEALTHY

#### Technical Details

- Root cause: `_check_video_corruption()` at line 1343 was adding warnings to `warning_details`
- This caused `has_warnings = True` even though warnings are benign
- Fix: Log the warnings for debugging but don't add to warning_details

#### Files Modified

- `media_checker.py` - Removed warning_details assignment for benign DTS/PTS warnings

---

## [2.5.20] - 2025-12-03

### Fix: HEVC PPS False Positive in Corruption Detection

#### Fixed

- **HEVC "PPS changed between slices" no longer triggers false positive corruption**
  - Added to benign patterns list in multi-point sampling
  - Common in Blu-ray remux content where dynamic parameter sets are used
  - Files with only PPS warnings now marked as HEALTHY (not corrupted)

- **Added additional HEVC benign patterns**
  - `'skipping nal unit'` - Filler/padding NAL units (type 63)
  - `'last message repeated'` - FFmpeg continuation line format

#### Changed

- **Improved warning logging format**
  - Now shows counts: "14 PPS parameter changes, 9 NAL unit skips in middle"
  - Files with only benign warnings stay HEALTHY (no warning_details added)

#### Technical Details

- Root cause: Multi-point sampling in `_check_multipoint_sampling()` was flagging legitimate HEVC stream features
- PPS (Picture Parameter Set) changes are part of HEVC spec for adaptive encoding
- NAL unit 63 is reserved/unspecified, often used for Blu-ray markers

#### Files Modified

- `media_checker.py` - Updated benign_patterns list and warning logging

---

## [2.5.18] - 2025-11-25

### Feature: Enhanced Version API Endpoint with Infrastructure Versions

#### Added

- **`/api/version` endpoint now returns infrastructure component versions**
  - Celery version (task queue)
  - Redis version (message broker)
  - PostgreSQL version (database)
  - Example response includes `infrastructure: { celery: "5.3.4", redis: "7.2.3", postgresql: "15.2" }`

- **`/api/version` endpoint added to OpenAPI documentation**
  - New "System" tag category for system information endpoints
  - Full schema documentation for version response

#### Changed

- **Removed `tools/update_version.py`** - Version should only be updated manually in `version.py`
- **`openapi.yaml` version is now dynamic** - Served via `/api/openapi.yaml` with version injected from `version.py` at runtime
- **Static `openapi.yaml` uses placeholder version `0.0.0`** - Indicates it's dynamically set

#### Technical Details

- `version.py` is the single source of truth for application version
- All version references read from `version.py` dynamically
- Infrastructure versions queried at runtime from actual services

#### Files Modified

- `app.py` - Enhanced `/api/version` endpoint with infrastructure versions
- `openapi.yaml` - Added `/api/version` endpoint documentation, added "System" tag
- Deleted: `tools/update_version.py`

## [2.5.17] - 2025-11-25

### Fix: Complete Stale Scheduler Lock Fix (v2.5.16 was incomplete)

#### Fixed

- **v2.5.16 fix was incomplete - existing stale lock was not cleared**
  - v2.5.16 only changed TTL for NEW locks from 24h to 60s with heartbeat
  - But existing stale lock (created with 24h TTL by v2.5.15) still blocked scheduler
  - Fix: Now explicitly deletes any stale lock on startup BEFORE attempting to acquire
  - Safe because on fresh container startup, any existing lock is from a dead process
  - Workers within same container race to acquire after delete, first one wins

#### Technical Details

- Added `redis_client.delete(lock_key)` before `set(..., nx=True)` in `app.py`
- Added log message: "Cleared stale scheduler lock (if any) on startup"

#### Files Modified

- `app.py` - Added delete of stale lock before acquiring new lock

## [2.5.16] - 2025-11-25

### Fix: Stale Scheduler Lock Prevents Scheduled Tasks After Container Restart (INCOMPLETE)

#### Fixed

- **Scheduler not starting after container restart due to stale Redis lock**
  - Root cause: Redis scheduler lock used 24-hour TTL (86400 seconds)
  - When container is replaced during deployment, old container's lock persists
  - New container finds existing lock and skips scheduler initialization
  - Result: No scheduler running - all scheduled scans fail to execute
  - Fix: Changed lock TTL from 24 hours to 60 seconds with heartbeat refresh
  - Added background thread that refreshes the lock every 30 seconds
  - Stale locks from crashed containers now expire within 60 seconds
  - Active schedulers maintain their lock indefinitely through heartbeat

#### Technical Details

- Modified scheduler lock logic in `app.py` lines 721-755
- Lock TTL reduced from `ex=86400` to `ex=60`
- Added daemon thread `refresh_scheduler_lock()` that runs every 30 seconds
- Thread refreshes lock with new timestamp and 60-second TTL
- Worst case recovery time: 60 seconds after container crash

#### Files Modified

- `app.py` - Changed lock TTL and added heartbeat thread

## [2.5.15] - 2025-11-25

### Fix: Healthcheck Completion Ping Not Sent When Scan Already Completed

#### Fixed

- **No completion ping sent when scheduled scan detects existing completed scan**
  - Root cause: When Celery task detects that a scan with the same `scan_id` (e.g., `scheduled_11`) already exists in `completed` phase, it returns early to prevent duplicate work
  - This early return bypassed `_create_scan_report()` which calls `send_healthcheck_completion()`
  - Result: Start ping sent by scheduler, but no completion ping ever sent
  - Fix: When returning early for "already completed" scans, still send completion ping since the scheduler already sent a start ping and healthcheck services expect a completion signal
  - Now finds the existing ScanReport for the scan_id and sends the completion ping before returning early

#### Technical Details

- Modified early-return path in `scan_media_task()` at `pixelprobe/tasks.py:100-126`
- When `scan_id.startswith('scheduled_')` and scan is already completed, queries for existing `ScanReport` and calls `MediaScheduler.send_healthcheck_completion()`
- Added `ScanReport` to imports in tasks.py

#### Files Modified

- `pixelprobe/tasks.py` - Added completion ping to early-return path for already-completed scans

## [2.5.14] - 2025-11-25

### Fix: Schedule Re-enable Does Not Update next_run Time

#### Fixed

- **Re-enabling a disabled schedule shows stale next_run time**
  - Root cause: In multi-worker Gunicorn deployment, scheduler runs in only ONE worker
  - When API request to re-enable schedule arrives, it may be handled by a worker without the scheduler
  - `scheduler.update_schedules()` returns early if `not self.scheduler.running`
  - The `next_run` is never recalculated for re-enabled schedules
  - Fix: Calculate `next_run` directly in the API endpoint using APScheduler's `CronTrigger.get_next_fire_time()`
  - This works without requiring a running APScheduler instance
  - Supports both cron expressions (e.g., "*/5 * * * *") and interval expressions (e.g., "interval:hours:6")
  - Also recalculates `next_run` when cron expression is changed on an active schedule

#### Technical Details

- Added `calculate_next_run()` helper function to `admin_routes.py`
- Uses APScheduler's `CronTrigger` class which can calculate next fire time without a running scheduler
- Detects when schedule transitions from `is_active=False` to `is_active=True`
- Also triggers recalculation when `cron_expression` changes on an active schedule

#### Files Modified

- `pixelprobe/api/admin_routes.py` - Added `calculate_next_run()` helper and updated `update_schedule()` endpoint
- `tests/integration/test_admin_endpoints.py` - Added tests for schedule reactivation scenarios

## [2.5.13] - 2025-11-25

### Fix: Scheduled Scan Duplicate Key + DetachedInstanceError + Celery Retry

#### Fixed

- **Scheduled scans fail with duplicate key constraint violation**
  - Root cause: `scan_id` has unique constraint in ScanState model
  - When a scheduled scan runs again, it tries to INSERT with same scan_id (e.g., "scheduled_1")
  - Previous scan_state record remains in database after completion
  - Fix: `create_new_scan()` now deletes existing scan_state with same scan_id before creating new
  - Scheduled scans can now run repeatedly without constraint violations

- **DetachedInstanceError after scan completion**
  - Root cause: `db.session.close()` in retry loop at line 729 detaches all SQLAlchemy objects
  - `final_scan_state` was fetched before retry loop but accessed after potential session closure
  - Fix: Re-fetch `final_scan_state` after retry loop completes
  - Added graceful fallback if scan_state cannot be re-fetched

- **Celery retry creates duplicate scan state on already-completed scans**
  - Root cause: When Celery retries a task after DetachedInstanceError, scan may have already completed
  - Retry would try to create new scan_state with same scan_id
  - Fix: Check if scan_id already has completed phase before starting
  - Returns early with success if scan already finished

#### Technical Details

- `models.py`: `create_new_scan()` deletes existing scan_state with same scan_id before INSERT
- `scan_service.py`: Re-fetch final_scan_state after db.session.close() to avoid DetachedInstanceError
- `tasks.py`: Early-exit check if scan_id already has phase='completed'

#### Files Modified

- `models.py` - Delete existing scan_state before creating new one for same scan_id
- `pixelprobe/services/scan_service.py` - Re-fetch scan_state after retry loop
- `pixelprobe/tasks.py` - Skip retry if scan already completed

## [2.5.12] - 2025-11-25

### Fix: Orphaned Pending Files + FFmpeg Warning Format + Chunk Retry

#### Fixed

- **Orphaned pending files from failed scan chunks now get processed**
  - Root cause: SQLAlchemy concurrency error "This session is provisioning a new connection" caused scan chunks to fail silently
  - Scan would report success but files in failed chunks stayed in `pending` status forever
  - Fix 1: Remove directory filter from pending files query so ALL pending files are included in every scan
  - Fix 2: Add retry logic for failed chunks - failed chunks are retried sequentially after parallel processing completes
  - This ensures orphaned files from previous interrupted scans eventually get processed

- **FFmpeg warning details now show actual DTS/PTS counts**
  - Before: Generic "Benign FFmpeg warnings in middle section" message
  - After: Specific counts like "3 DTS, 2 PTS warnings in middle"
  - Helps identify actual warning severity without looking at raw logs

- **Failed scan chunks are now retried**
  - Before: If a chunk failed during parallel processing, it was logged and ignored
  - After: Failed chunks are tracked and retried sequentially after the main parallel loop
  - Sequential retry avoids the concurrency issues that caused the original failure

#### Technical Details

- Pending files from failed chunks (e.g., SQLAlchemy concurrency errors) are now recovered
- `scan_service.py` line 573: Removed directory filter from pending files count
- `scan_service.py` lines 1562-1590: Added failed chunk tracking and sequential retry
- `media_checker.py` lines 1915-1927: Parse stderr for DTS/PTS counts instead of generic message

#### Files Modified

- `pixelprobe/services/scan_service.py` - Include all pending files in scan, add chunk retry logic
- `media_checker.py` - Show actual DTS/PTS warning counts

## [2.5.11] - 2025-11-24

### Fix: Duplicate Scheduler Start Pings in Multi-Container Deployments

#### Fixed

- **Duplicate start pings due to scheduler running in multiple containers**
  - Bug: File-based lock (`fcntl.flock`) doesn't work across containers (each has separate `/tmp` filesystem)
  - Both gunicorn and celery-worker containers would acquire their own lock and initialize the scheduler
  - Fix: Replaced file lock with Redis-based distributed lock using `SETNX`
  - Uses `redis.set(lock_key, lock_value, nx=True, ex=86400)` for atomic acquisition
  - 24-hour expiry ensures auto-recovery if container crashes
  - Falls back to file lock if Redis unavailable (for local development)

#### Technical Details

- Lock key: `pixelprobe:scheduler:lock`
- Lock value: `{pid}:{timestamp}` for debugging
- Only the first container to acquire the Redis lock will initialize the scheduler
- Other containers log which process holds the lock

#### Files Modified

- `app.py` - Replace file-based lock with Redis distributed lock

## [2.5.10] - 2025-11-24

### Hotfix: Pass Scheduled Scan ID Through Entire Scan Pipeline

#### Fixed

- **Scheduled scan ID not preserved through scan pipeline**
  - Bug: Even though `/api/scan` now passes `scheduled_XX` as scan_id, `ScanService.scan_directories()` was creating a new UUID
  - The scan_id was not passed through: Celery task -> scan_directories() -> ScanState.create_new_scan()
  - Fix: Added `scan_id` parameter through the entire chain
  - `ScanState.create_new_scan(scan_id=None)` now accepts optional scan_id
  - `scan_directories(scan_id=None)` now passes it to create_new_scan
  - Celery task now passes scan_id to scan_directories

#### Files Modified

- `models.py` - `create_new_scan()` accepts optional scan_id parameter
- `pixelprobe/services/scan_service.py` - `scan_directories()` accepts and passes scan_id
- `pixelprobe/tasks.py` - Pass scan_id to scan_directories()

## [2.5.9] - 2025-11-24

### Hotfix: Scheduled Scan ID Not Passed to Healthcheck

#### Fixed

- **Scheduled scans not identified for healthcheck completion pings**
  - Bug: `/api/scan` endpoint ignored the `source` field from scheduler
  - The scheduler sent `source: 'scheduled_XX'` but endpoint generated a new UUID
  - `send_healthcheck_completion()` checks for `scan_id.startswith('scheduled_')` to identify scheduled scans
  - Since scan_id was always a UUID, completion pings were never sent
  - Fix: Use `source` as `scan_id` when it starts with `scheduled_`

#### Files Modified

- `pixelprobe/api/scan_routes.py` - Use scheduler source as scan_id for scheduled scans

## [2.5.8] - 2025-11-24

### Hotfix: Healthcheck Completion Ping Type Error

#### Fixed

- **Healthcheck completion ping failed with type error**
  - Bug: Passing `report.report_id` (UUID string) instead of `report.id` (integer primary key)
  - Error: `invalid input syntax for type integer: "UUID-string"`
  - Fix: Changed to pass `report.id` to `send_healthcheck_completion()`
  - Completion pings now work correctly for all scan types including empty scans (0 files)

#### Files Modified

- `pixelprobe/services/scan_service.py` - Fixed parameter type (report.id instead of report.report_id)

## [2.5.7] - 2025-11-24

### Critical Fix: Healthcheck Completion Pings + Mobile UI

#### Fixed

- **Healthcheck Completion Pings Now Working**
  - CRITICAL BUG: `send_healthcheck_completion()` was defined but never called
  - Start pings worked, but success/failure pings on scan completion were never sent
  - Added call to `MediaScheduler.send_healthcheck_completion()` in `scan_service._create_scan_report()`
  - Now properly sends success ping when scan completes successfully
  - Now properly sends failure ping when scan fails/errors
  - Includes scan report data in success pings when configured

- **Improved Healthcheck URL Logging**
  - All ping methods now log the full URL being pinged
  - Logs include HTTP status code on success
  - Error messages now include the URL that failed
  - Helps debug connectivity issues with self-hosted instances

- **Self-Hosted Healthchecks.io Support**
  - Removed confusing warning message for non-standard URL formats
  - Supports any URL format (public hc-ping.com or self-hosted instances)
  - Self-hosted URLs can use custom paths, not just `/ping/UUID`

#### Changed

- **Schedule Action Buttons - Now All Icons**
  - Disable button: Changed from text "Disable" to pause icon (`fa-pause`)
  - Enable button: Changed from text "Enable" to play icon (`fa-play`)
  - All four buttons now use icons only: edit, healthcheck, pause/play, trash
  - Added title tooltips for accessibility

- **Uniform Button Sizing on Mobile**
  - All schedule action buttons now have fixed 2.25rem width/height
  - Buttons use flexbox centering for consistent icon alignment
  - Removed variable sizing that caused layout issues

#### Files Modified

- `pixelprobe/services/scan_service.py` - Added healthcheck completion ping call
- `scheduler.py` - Updated method signature for easier calling
- `pixelprobe/services/healthcheck_service.py` - Improved logging, removed restrictive URL warning
- `static/js/app.js` - Changed Enable/Disable to icons
