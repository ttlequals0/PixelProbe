# Audit remediation plan

Source audit: September 10, 2026, commit `3e759b068ce67452180e1d08e8b44189c57ff763`.

This is the implementation and verification ledger for the combined repair PR. A finding remains open until its behavior and regression checks have been reviewed. Historical report membership cannot be reconstructed from mutable current results; legacy reports must say when their evidence is incomplete.

| Findings | Required outcome | Status |
|---|---|---|
| S01, R13 | Authorized supported media at every file boundary; safe conditional responses and active-content downloads | In progress |
| S02, R12, E02 | Safe DOM rendering, explicit pending/error verdicts, one polling owner | In progress |
| S03-S07 | Enforced roles, cookie CSRF, effective limits, secure cookies, session revocation | In progress |
| S08-S09 | Connection-bound outbound policy, token digests, redacted and attributable audit events | In progress |
| S10 | Non-root containers with bounded resources and writable runtime directories | Pending |
| R01-R04, R08-R09 | Scoped run membership, unavailable-root outcomes, preserved baselines, successful coverage, consistent run IDs | In progress |
| R05-R07, R10, R18 | Owned cancellation, idempotent asynchronous work, immutable reports, coordinated startup | In progress |
| R11, R19 | Validated destructive filters and schedule definitions | In progress |
| R14, E01 | Literal PDF text, accurate export limits, bounded result selection and streaming | Pending |
| R15 | Working source assets or an explicit build requirement | In progress |
| R16 | Supported notification events and conditions with delivery evidence | Pending |
| R17 | Exact CSV parsing, authorized targets, dry-run default, explicit application | Pending |
| E03-E04 | Bounded cleanup and waiting, worker-owned logging with run context | In progress |
| E05 | Retire or repair confirmed stale paths without removing task-registration side effects | Pending |
| D01-D14 | Documentation, API schema, license, effective configuration, recovery and evidence definitions | Pending |
| README and onboarding | Match the requested README structure; consolidate accurate repository onboarding | Pending |

## Verification

- Run the complete Python suite in the virtual environment, including PostgreSQL and real-media coverage.
- Exercise production initialization for authentication, cookies, CSRF, rate limiting, migrations, and assets.
- Exercise real workers for duplicate delivery, cancellation isolation, partial dispatch, and minimal concurrency.
- Verify actual DOM rendering and media range responses.
- Verify immutable report selection and bounded exports, including PDF markup characters and large selections.
- Run the frontend build and application startup check.
- Review the complete diff and check each audit ID before opening the PR.
- Run CI and CodeQL before any production image build or push, then validate the non-root image with real media.

## Additional runtime evidence

Read-only runtime inspection found historical startup migration failures followed by endpoint schema errors, and database name-resolution failures followed by serving attempts. Current scan logs also contain missing-file errors during metadata reads and hashing. These observations inform R02, R03, and R18; they do not establish that the historical schema failure remains active.

Cleanup logs showed the existing missing-storage safeguard aborting deletion. Preserve that behavior while bounding cleanup memory and polling.

The live version and published image tags are ahead of the audited source. Do not infer that untracked release changes are present on main, and do not reuse an existing image version.
