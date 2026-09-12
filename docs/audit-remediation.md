# Audit remediation plan

Source audit: September 10, 2026, commit `3e759b068ce67452180e1d08e8b44189c57ff763`.

This is the implementation and verification ledger for the combined repair PR. A finding remains open until its behavior and regression checks have been reviewed. Historical report membership cannot be reconstructed from mutable current results; legacy reports must say when their evidence is incomplete.

| Finding | Required outcome | Implementation evidence | Validation state |
|---|---|---|---|
| S01 | File authorization | `scan_launch.py`, `scan_routes.py`, and filesystem boundary helpers apply configured-root checks. | Complete local suite passed. Real-media production-image validation remains pending. |
| S02 | Stored XSS and active SVG | Frontend renderers and export/view routes use safe text handling and active-content controls. | Complete local suite and frontend DOM regressions passed. |
| S03 | Administrator permissions | `auth.py`, route decorators, and admin routes enforce administrator-only operations. | Complete local suite and production-initialization security matrix passed. |
| S04 | CSRF | `app.py` applies CSRF protection to all cookie-authenticated writes, including session authentication routes. | Complete local suite and production-initialization security matrix passed. |
| S05 | Rate limiter | `rate_limiting.py` and route decorators use the configured shared limiter storage. | Complete local suite and production-initialization security matrix passed, including two initialized web processes and Redis 429 behavior. |
| S06 | Cookies | `config.py`, Compose, and API documentation set secure, HTTP-only, SameSite, and remember-duration behavior. | Complete local suite and production-initialization security matrix passed. |
| S07 | Credential revocation | Password changes increment session generation and invalidate browser sessions; API tokens require independent revocation, expiry, or owner deactivation. | Complete local suite and production-initialization security matrix passed. |
| S08 | SSRF | `utils/security.py` and notification and healthcheck services validate outbound destinations and connections. | Complete local suite and local outbound-policy regressions passed. Live outbound requests were intentionally not used. |
| S09 | Token digests and redacted actor logs | Authentication models and security-audit migration retain actor evidence without storing raw tokens. | Complete local suite passed. |
| S10 | Non-root resources | `Dockerfile` and Compose configure non-root PUID:PGID, read-only media, runtime tmpfs, and resource limits. | Implementation present; production-image validation pending. |
| R01 | Scope, schedules, and exclusions | Scheduler, maintenance routes, and maintenance service pass validated `scan_roots` and preserve exclusions. | Complete local suite passed. |
| R02 | Missing and unreadable roots | Mount and filesystem preflight code records unavailable outcomes and blocks destructive work on approved-mount mismatch. | Complete local suite passed. Live mount and NFS validation remains pending. |
| R03 | Baseline preservation | Integrity and lifecycle migration code preserve known baselines and normalize known failed legacy observations. | Complete local suite and PostgreSQL migration regression passed. |
| R04 | Successful coverage | Stats and maintenance results distinguish attempted, successful, error, and unavailable observations. | Complete local suite passed. |
| R05 | Owned cancellation | Scan tasks and lifecycle ownership fields restrict cancellation to the active run. | PostgreSQL concurrency and real-worker validation passed. |
| R06 | Redelivery | Durable task intent and recovery paths make duplicate task delivery idempotent. | PostgreSQL concurrency and real-worker recovery validation passed. |
| R07 | Immutable atomic reports | `ScanRunRoot`, `ScanRunFile`, and report routes use run-scoped evidence rather than mutable inventory. | Focused report regressions and complete local suite passed. |
| R08 | Double-encoded directories | Report directory parsing accepts the historical double-encoded array format. | Focused regression and complete local suite passed. |
| R09 | Selected-run reservation | Scan reservation and task dispatch use durable run identifiers and selected-run membership. | PostgreSQL concurrency and real two-worker selected-scan validation passed. |
| R10 | Readiness and no child wait | Worker detection utilities, Celery helpers, and asynchronous task dispatch avoid child-process wait paths. | Complete local suite, readiness regression, one-slot solo worker, and Linux prefork validation passed. |
| R11 | Purge filters | `api/log_routes.py` validates destructive log-purge filters before execution. | Complete local suite passed. |
| R12 | Status | Frontend and API status handling retain explicit pending, error, unreadable, and unknown states. | Complete local suite and frontend DOM regressions passed. |
| R13 | Ranges | Export and view routes validate and authorize media ranges before serving content. | Complete local suite and Flask response range-edge regressions passed. |
| R14 | PDF | Report PDFs escape untrusted text, use bounded detail, and avoid loading unused scan output. | Focused export regression and complete local suite passed. |
| R15 | Assets | Source assets and manual frontend build requirements are documented and tested. | Fresh `npm ci`, DOM test, and production build passed. |
| R16 | Notification contracts | Notification providers, event rules, conditions, outbox, and delivery records define supported contracts. | PostgreSQL delivery regressions and complete local suite passed. |
| R17 | CSV delete tool | `delete_files_from_csv.sh` validates roots, defaults to dry run, and requires `--apply`. | Tool regressions and complete local suite passed. |
| R18 | Migration and scheduler locks | Startup migrations use PostgreSQL advisory ownership; scheduler lock manages lease loss and shutdown. | PostgreSQL startup regressions, production startup check, and complete local suite passed. |
| R19 | Schedule validation | Admin schedule routes validate expressions, paths, scan types, and time budgets. | Complete local suite passed. |
| E01 | Bounded exports | Export and report routes use keyset batches, initial high-watermarks, release request transactions, and cap PDF detail. | Focused export and PostgreSQL route-parity regressions plus complete local suite passed. |
| E02 | Duplicate pollers | Frontend state and polling ownership prevent duplicate active pollers. | Complete local suite and frontend DOM regressions passed. |
| E03 | Cleanup memory and busy polling | Cleanup candidates and decisions use bounded batches; active task waits poll only while tasks remain. | Complete local suite passed. |
| E04 | Logging handler and context | Database logging and task context keep worker logging durable and attributed. | `tests/test_logs.py` unit context and handler checks passed. Linux prefork persisted directory-context logs, but per-child handler and context-reset validation remains pending. |
| E05 | Dead stale paths | Confirmed stale paths were retired or repaired without dropping task-registration side effects. | Complete local suite passed. |
| D01 | Roles and CSRF documentation | `docs/api.md` publishes the route permission matrix and cookie CSRF requirement. | Documentation review and complete local suite passed. |
| D02 | Cookie documentation | `docs/api.md` records cookie and remember-duration behavior. | Documentation review and complete local suite passed. |
| D03 | Browser CSRF flow | `docs/api.md` distinguishes browser CSRF from Bearer and internal authentication. | Documentation review and complete local suite passed. |
| D04 | Notification documentation | `docs/operational-evidence.md` lists supported notification events, conditions, providers, and limits. | Documentation review and complete local suite passed. |
| D05 | Batching documentation | `docs/configuration.md`, `docs/docker-setup.md`, and `docs/performance-tuning.md` identify `BATCH_SIZE` as a legacy lookup setting, not parallel discovery or chunk control. | Documentation review and Compose validation passed. |
| D06 | Output rotation documentation | `docs/configuration.md` records the current character limit and model-level rotation; worker configuration references the root Compose file. | Documentation review and complete local suite passed. |
| D07 | Environment forwarding documentation | `docs/configuration.md` and `docs/docker-setup.md` name root `docker-compose.yml` as the effective web and worker configuration. | Documentation review and Compose validation passed. |
| D08 | Image tag documentation | `docs/docker-setup.md` directs deployments to the current root Compose image selection. | Documentation review passed. Production-image validation remains an S10 gate. |
| D09 | Manual assets documentation | `docs/developer-guide.md` states the asset build requirement and command. | Fresh frontend build passed. |
| D10 | Async single-file documentation | `README.md` and `docs/operational-evidence.md` describe asynchronous scan completion. | Documentation review and complete local suite passed. |
| D11 | Architecture documentation | `docs/how-it-works.md` describes application, worker, broker, database, scheduler, and migrations. | Documentation review and production startup check passed. |
| D12 | MIT | `LICENSE` contains the approved MIT license. | Repository license review passed. |
| D13 | OpenAPI | `openapi.yaml` and route-parity regression cover all registered API operations. | PostgreSQL parity regression and complete local suite passed. Field coverage remains partial as stated in the specification. |
| D14 | Integrity and report metrics | `docs/operational-evidence.md` defines immutable evidence and distinct attempt, success, and latest-outcome meanings that are not additive. | Documentation review and complete local suite passed. |
| README and onboarding | Requested structure and safe workflow | `README.md`, `CONTRIBUTING.md`, and shared onboarding provide current setup and handoff instructions. | Documentation review, fresh frontend build, and production startup check passed. |

## Documentation evidence, D01-D14

| Finding | Evidence | Verification state |
|---|---|---|
| D01 | `docs/api.md` publishes the route permission matrix and states that browser-session writes, including login, logout, and setup, require CSRF protection. | Production authentication and CSRF behavior remains a release gate. |
| D02 | `docs/api.md` records secure, HTTP-only, SameSite cookie defaults, the 30-day remember default, and the explicit HTTP-only secure-cookie override. | Production cookie behavior remains a release gate. |
| D03 | `docs/api.md` documents the browser session CSRF flow separately from Bearer and internal scheduler authentication. | Browser flow review remains a release gate. |
| D04 | `docs/operational-evidence.md` lists supported notification events, condition keys, providers, and delivery evidence limits. | PostgreSQL delivery regressions and the complete local suite passed. |
| D05 | `docs/configuration.md`, `docs/docker-setup.md`, and `docs/performance-tuning.md` distinguish legacy `BATCH_SIZE` lookup batches from parallel discovery and chunk control. | `docker compose config -q` passed. |
| D06 | `docs/configuration.md` documents `MAX_OUTPUT_SIZE` in characters and model-level `OUTPUT_ROTATION_ENABLED`; worker settings are supplied by root Compose. | Complete local suite passed. Real-worker output-rotation release validation remains pending. |
| D07 | `docs/configuration.md` and `docs/docker-setup.md` point to root Compose for effective web and worker forwarding. | `docker compose config -q` passed. |
| D08 | `docs/docker-setup.md` directs deployment to root Compose for current image selection. | Production-image validation remains an S10 release gate. |
| D09 | `docs/developer-guide.md` states the asset build requirement and manual frontend build command. | Fresh `npm ci` and production build passed. |
| D10 | `README.md` and `docs/operational-evidence.md` state that a single-file scan request is asynchronous and completion requires status or report polling. | Complete local suite passed. |
| D11 | `docs/how-it-works.md` describes the application, worker, broker, database, scheduler, and migration architecture. | Documentation review and production startup check passed. |
| D12 | `LICENSE` contains the approved MIT license with PixelProbe contributors as copyright holder. | Repository license review passed. |
| D13 | `openapi.yaml` documents every registered API operation. `tests/test_openapi_route_parity.py` compares normalized Flask and OpenAPI method/path sets in the fixture and in a PostgreSQL production-app import; static and UI rules have named exceptions. Response field coverage remains partial as warned by the specification. | PostgreSQL parity regression and complete local suite passed. |
| D14 | `docs/operational-evidence.md` defines immutable report evidence and cumulative integrity attempt and success counters separately from latest error and unreadable outcomes. These counters are not additive. | Documentation review and complete local suite passed. |

## Export and report evidence

| Finding | Evidence | Verification state |
|---|---|---|
| R07 | Reports treat immutable `ScanRunFile` members or `ScanRunRoot` rows as new-run evidence. A legacy `ScanState` alone no longer makes missing history appear available. | Focused legacy-state and unavailable-root regression passed locally. |
| R08 | Historical double-encoded directory arrays remain readable as arrays for older reports. | Focused compatibility regression passed locally. |
| R14, E01 | Generic CSV and JSON exports freeze an initial max ID and release the request transaction before streaming. Individual report CSV and JSON stream immutable rows; PDFs select needed columns and cap detail. | Focused export suite used 999, 1000, and 1001 rows with 10 KB `scan_output` each. It asserts no request transaction during a paused stream, a maximum of two detail queries for the per-run plus combined PDF fixture, and Python allocation below 64 MiB. This is a regression threshold, not a production RSS benchmark. The complete local suite passed. |

## Deferred product suggestions

These are not closed audit findings. No documentation claim should imply they are implemented.

| Suggestion | Current position |
|---|---|
| Scoped API tokens | Not implemented by this repair set. Existing API tokens retain current scope. |
| Offline packaging | Not implemented by this repair set. |
| Explicit administrator bitrot-baseline approval policy | Not implemented by this repair set. Baseline acceptance remains an operator and policy decision. |

## Verification

### Current local checkpoint

With `PATH` resolving Node 22.22.2, a task-owned PostgreSQL URI in
`PIXELPROBE_TEST_POSTGRES_URI`, and a task-owned Redis URI in
`PIXELPROBE_TEST_REDIS_URI`, `CELERY_BROKER_URL`, and
`CELERY_RESULT_BACKEND`, the following command passed on September 12, 2026:

```bash
venv/bin/python -m pytest tests/ --tb=short --timeout=60 -q -ra
```

Result: 830 passed, 1 skipped, in 200.35 seconds. The sole skip is
`tests/test_media_checker.py:931`, where this filesystem does not report sparse
regions. Linux CI must cover that platform-specific case.

Additional local checks passed: `npm audit` reported 0 vulnerabilities,
`pip-audit -r requirements.txt` reported no known vulnerabilities, `docker
compose config -q` passed, and F821 plus `git diff --check` passed. A fresh
Node 22.22.2 `npm ci`, `npm run test:dom`, and `npm run build` passed; Webpack
5.110.3 compiled in 810 ms. Gunicorn 23.0.0 started the production application
with one worker against isolated PostgreSQL and Redis. It completed migration
and schema readiness, served `GET /healthz` with HTTP 200, and terminated its
owned master cleanly.

Limits: local tests do not validate a live NFS or approved-mount failure, or
production-scale RSS. S10 production-image validation with real media remains
pending. The sparse-region case remains for Linux CI.

### Reproducible worker recovery validation

Use a task-owned disposable PostgreSQL database and isolated Redis database. Do
not run this against an operator database or a shared test queue.

```bash
export POSTGRES_DB="$PIXELPROBE_WORKER_TEST_DB"
export CELERY_BROKER_URL="$PIXELPROBE_WORKER_REDIS_URI"
export CELERY_RESULT_BACKEND="$PIXELPROBE_WORKER_REDIS_URI"
export RATELIMIT_STORAGE_URI="$PIXELPROBE_WORKER_REDIS_URI"
export SCHEDULER_ENABLED=false
source venv/bin/activate
celery -A app.celery worker -P solo --queues=pixelprobe --loglevel=WARNING
```

In another terminal, submit two canonical paths through `POST
/api/scan-files-parallel` with `file_paths` and `num_workers: 2`. Record the
returned `scan_id` and `task_id`, then verify that the matching `ScanState`,
`ScanTask`, one `ScanReport`, and exactly two `ScanRunFile` rows are terminal.
The selected-worker validation recorded one generated task ID in the state and
intent, two completed ffmpeg observations with hashes and mtime baselines,
and a completed report with two scanned files and zero corrupt files. The
focused durable regressions are reproducible with:

```bash
PIXELPROBE_TEST_POSTGRES_URI="$PIXELPROBE_TEST_POSTGRES_URI" \
CELERY_BROKER_URL="$PIXELPROBE_TEST_REDIS_URI" \
CELERY_RESULT_BACKEND="$PIXELPROBE_TEST_REDIS_URI" \
python -m pytest -q tests/test_scan_task_recovery_postgres.py \
  tests/integration/test_scan_execution.py \
  tests/test_concurrency.py::TestConcurrency::test_scan_cancellation_race
```

### Remaining release steps

- Review the complete diff and check each audit ID before opening the PR.
- Run CI and CodeQL before any production image build or push.
- Complete S10 production-image validation with real media: verify configured UID/GID ownership, read-only media, writable runtime paths, resource limits, prefork startup, and recovery.
- Run the sparse-region case on Linux CI and validate a live NFS or approved-mount failure.

## Additional runtime evidence

Read-only runtime inspection found historical startup migration failures followed by endpoint schema errors, and database name-resolution failures followed by serving attempts. Current scan logs also contain missing-file errors during metadata reads and hashing. These observations inform R02, R03, and R18; they do not establish that the historical schema failure remains active.

Cleanup logs showed the existing missing-storage safeguard aborting deletion. Preserve that behavior while bounding cleanup memory and polling.

The live version and published image tags are ahead of the audited source. Do not infer that untracked release changes are present on main, and do not reuse an existing image version.
