# Web interface

The management UI: dashboard, scan results, file actions, schedules, and the admin views, with screenshots.

## Dashboard

The dashboard shows stat cards (total, corrupted, warnings, bitrot, healthy, pending, integrity coverage), filter buttons that mirror the card order, and the results table.

### Light mode

![Desktop light mode](screenshots/desktop-light.png)

Statistics dashboard, sortable results table with bulk actions, and sidebar navigation.

### Dark mode

![Desktop dark mode](screenshots/desktop-dark.png)

Full feature parity with a high-contrast dark theme. The preference persists across sessions.

### Mobile

<div align="center">
  <img src="screenshots/mobile-light-dashboard.png" alt="Mobile light dashboard" width="300" style="margin: 10px">
  <img src="screenshots/mobile-dark-dashboard.png" alt="Mobile dark dashboard" width="300" style="margin: 10px">
</div>

The mobile layout has a collapsible sidebar and shows scan results as cards instead of a table.

## Working with results

1. Start a scan with "Scan All Files"; progress, ETA, and phase appear live.
2. Filter with the buttons above the table (corrupted, warnings, bitrot, healthy, pending) or the path dropdown.
3. Select files with checkboxes; Shift+click selects ranges.
4. Per-file actions:
   - View: stream and preview media in the browser
   - Rescan: re-examine one file
   - Download: save the file locally
   - Mark as Good: clear false positives (bulk up to 1000 files)
   - Integrity Check: verify the file still exists and has not changed
   - Details: open the Scan Details modal - verdict sections first, with the raw tool transcript collapsed behind "Full scan transcript"

## Authentication and user management

### Login

![Login screen](screenshots/auth/login.png)

Username/password login with remember-me. On first run the login page redirects to the setup wizard to create the admin account.

### User management

![User management](screenshots/auth/user_management.png)

Create, view, and delete user accounts with role-based access control.

### API tokens

![API tokens](screenshots/auth/api_tokens.png)

Generate and revoke API tokens for programmatic access. See [API reference](api.md) for how to use them.

### Password management

![Change password](screenshots/auth/change_password.png)

Change password with current-password verification.

## Admin views

### Scan reports

![Scan reports](screenshots/features/scan-reports.png)

Past scan operations with statistics, filterable by scan type, exportable as JSON or PDF.

### Scheduled scanning

![Scan schedules](screenshots/features/scan-schedules.png)

Automated scans on cron expressions. Supports normal scan, cleanup, and integrity check types; integrity schedules can carry a per-run time budget.

### Healthcheck monitoring

![Healthcheck configuration](screenshots/features/healthcheck-config.png)

Integrates with [Healthchecks.io](https://healthchecks.io/) or self-hosted instances. Sends start, success, and failure pings per schedule, with optional scan summary data.

### Trend analytics

![Trend analytics](screenshots/features/trends-analytics.png)

Corruption rates, storage growth, and performance metrics across 30/60/90-day and 1-year windows, with per-type breakdowns and growth projections.

### Exclusions management

![Exclusions management](screenshots/features/exclusions-management.png)

Skip directories or file extensions. Changes take effect on the next scan without a restart.

### Other sidebar items

- Ignored Errors: suppress known benign FFmpeg error patterns
- View Logs: application logs with level/time/search filtering, auto-refresh, and download
- System Stats: infrastructure versions and worker status
- Build Info: running version and image details

[< Documentation index](README.md)
