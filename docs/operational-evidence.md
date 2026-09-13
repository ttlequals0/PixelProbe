# Operational evidence and limits

This page describes what the application records and what it cannot reconstruct after an incident.

| Area | Evidence | Limit |
|---|---|---|
| Scan request | Request, state, and durable task ownership | HTTP acceptance is not scan completion. Poll status or read the final report. |
| New scan reports | `ScanRunRoot` and `ScanRunFile` capture requested roots and observed file outcomes | A root record proves the new evidence format even when a root is unavailable. Legacy reports without roots or members cannot recover historical membership from current `scan_results`. |
| Cleanup | `CleanupFileDecision` retains pending, eligible, held, returned, and removed-inventory decisions by immutable cleanup run ID | Cleanup removes inventory records, not media. It keeps records when it cannot distinguish deletion from unavailable storage. |
| Cleanup report detail | A report stores a 100-path preview, total, and truncation state. Complete JSON export streams the decision ledger. | PDF and visible previews are deliberately bounded. |
| Scan report exports | Individual immutable report CSV and JSON exports stream complete durable rows. | Generic and combined PDF exports cap synchronous detail. Use an individual CSV or JSON export for complete report membership. |
| Integrity coverage | `last_integrity_attempt_at`, `last_integrity_success_at`, and latest outcome are stored independently | A successful verification remains historical evidence even when a later attempt errors. It does not prove current content after that error. Do not subtract overlapping counts. |
| Notification delivery | The scan notification outbox stores selected targets, conditions, attempts, and terminal outcomes | Delivery is at least once. A crash after provider acceptance can duplicate a notification. |
| Audit events | Actor numeric IDs and event data survive account deletion | Audit history does not restore deleted accounts or media. |

## Integrity meanings

The dashboard and API report cumulative and latest-outcome values separately.

| Field | Meaning |
|---|---|
| `attempted_files` | Files with at least one recorded integrity attempt. |
| `checked_files` | Files with at least one successful stable hash verification. This is historical coverage, not a healthy-media verdict. |
| `integrity_error_files` | Files whose latest integrity outcome is `error`. |
| `integrity_unavailable_files` | Files whose latest integrity outcome is `unreadable`. |
| Per-run `integrity_attempted` | Terminal integrity attempts in that file-changes run, including work drained after a time budget. |
| Per-run `integrity_successful`, `integrity_errors`, `integrity_unavailable` | The mutually exclusive result of each terminal run attempt. |

An attempted count can overlap successful history and a later error. Latest errors and unreadable outcomes can also overlap an earlier successful check. It is not valid to sum these counters or calculate unavailable files as attempted minus successful minus errors. A completed media observation can still be a corruption finding.

Baseline acceptance remains an operator and policy decision. PixelProbe preserves observations and does not treat an older successful integrity check as approval of later content.

For full scans and rescans, report `success_rate` is successful non-corrupt completed observations divided by terminal observations, where terminal observations are completed observations plus errors. Empty runs and error-only runs report `0`, not `100`.

## Required mount policy

An administrator can set `require_mount=true` for a scan configuration. The first explicit approval stores the current Linux filesystem type, source, and mount root. Later scan, cleanup, and file-change work compares the root to that approved baseline before starting. Set `refresh_mount_baseline=true` only with `require_mount=true` to explicitly accept a changed mount.

`GET /api/configurations` exposes `require_mount` but not the stored mount details. A mismatch returns HTTP 503 with `Required storage mount is unavailable or does not match its approved baseline`. Configurations without `require_mount` continue to support ordinary directories.

## Notification events and conditions

Rules accept only the event types and condition keys below. Numeric values can use `>`, `>=`, `<`, `<=`, or an object with `operator` and `value`. String values use equality.

| Event | Condition keys |
|---|---|
| `scan_completed` | `scan_id`, `status`, `files_scanned`, `corrupted_count`, `warning_count`, `error_count` |
| `bitrot_suspected` | `count` |

Providers are `email`, `pushover`, `ntfy`, and `webhook`. Healthchecks.io is configured per scan schedule, not as a notification-rule provider.

## Recovery and restore verification

After a restart or restore:

1. Confirm PostgreSQL and Valkey health checks pass.
2. Confirm `GET /healthz` responds, then authenticate to `GET /health` and `GET /api/version`.
3. Confirm the worker is connected. In the standard Compose deployment, only `celery-worker` owns scheduler work.
4. Run a small scan of a disposable directory. Wait for a terminal state and inspect its report, rather than treating the launch response as completion.
5. Compare the recorded hash for a known baseline file with its expected value. Treat a mismatch, error, or unreadable result as an operational outcome to investigate, not a successful coverage count.
6. Check that media is still read-only and that both containers can write only to their intended runtime and instance locations.
7. After a database restore, run startup migrations, compare the expected application version, and document any legacy report history that cannot be reconstructed.
8. Revoke credentials that should not survive the restore. Confirm a revoked API token is rejected and rotate `SECRET_KEY` when all existing browser sessions must be invalidated.
