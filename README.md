<div align="center">
  <img src="static/images/pixelprobe-logo.svg" alt="PixelProbe" width="200" height="200">
</div>

PixelProbe is a self-hosted media checker. It scans video, image, and audio libraries for corruption, keeps scan history, and rechecks stored files for content changes.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Disclaimer](#disclaimer)
- [License](#license)
- [LLM disclosure](#llm-disclosure)

## Features

Detection

- FFmpeg validation for video and audio, plus PIL and ImageMagick decoding for images
- Warning and corruption verdicts kept separate
- Rolling hashes that flag content changes without a matching modification-time change
- Exclusions and per-finding mark-as-good overrides

Scanning and recovery

- Asynchronous discovery, chunked Celery scans, progress heartbeats, and restart recovery
- Scheduled scans, scoped maintenance, and a rolling integrity queue with optional time budgets
- Immutable run membership and observed outcomes for new scan reports
- Orphan cleanup removes inventory records only after filesystem evidence supports the decision. It never removes media files.

Operations

- PostgreSQL-backed reports, audit events, API tokens, and notification delivery records
- Email, Pushover, ntfy, webhook, and Healthchecks.io integrations
- JSON and PDF reports. Complete JSON exports stream durable history; visible and PDF detail is bounded.

## How it works

1. A scan request creates asynchronous work. A successful HTTP response means work was accepted, not that media validation finished.
2. Discovery records eligible paths and creates durable run membership.
3. Celery workers claim chunks and run the media checks.
4. Completion writes a report from the run snapshot. It does not reconstruct history from current library rows.
5. The integrity queue later hashes recorded files. It reports cumulative attempts and successful verification separately from the latest error and unreadable outcomes.

See [How it works](docs/how-it-works.md) and [Operational evidence](docs/operational-evidence.md) for recovery limits and historical-report behavior.

## Requirements

- Docker and Docker Compose
- A readable media directory on the host
- PostgreSQL and Valkey are included in the Compose stack

The app and worker run as a non-root UID/GID. The media mount is read-only. The host `instance` directory must be writable by the configured `PUID:PGID`.

## Quick start

Clone the repository or otherwise obtain its `docker-compose.yml`, then create a local `.env` from the example. Do not overwrite an existing `.env`; preserve it and review its values before changing anything. Keep `SESSION_COOKIE_SECURE=true` when accessing PixelProbe through HTTPS. For a local plain-HTTP deployment only, set it to `false` explicitly.

```bash
git clone https://github.com/ttlequals0/PixelProbe.git
cd PixelProbe
[ ! -e .env ] || { echo ".env already exists; review it instead of overwriting it"; exit 1; }
cp .env.example .env
```

Before startup, edit `.env`: replace `SECRET_KEY` and `POSTGRES_PASSWORD`, set `MEDIA_PATH`, and choose `PUID` and `PGID`. Create the host runtime directory and make it writable by those configured values. The Compose default is `10001:10001`.

```bash
mkdir -p instance
# Replace 10001:10001 if .env uses different PUID:PGID values.
sudo chown 10001:10001 instance
docker compose up -d
```

Change `SESSION_COOKIE_SECURE=false` only for local plain HTTP. Open `http://localhost:5000`, create the first administrator, and start a scan.

The default Compose configuration runs the web service with the scheduler disabled and the Celery worker as the scheduler owner. Both services use the same media mount and identity. See [Configuration](docs/configuration.md) before changing concurrency, memory limits, or database pools.

## Documentation

| Topic | Description |
|---|---|
| [Installation](docs/installation.md) | Docker and manual setup |
| [Configuration](docs/configuration.md) | Effective Compose values, cookies, permissions, schedules, and notifications |
| [How it works](docs/how-it-works.md) | Discovery, chunks, validation, reports, and recovery |
| [Operational evidence](docs/operational-evidence.md) | Async contract, immutable history, integrity meanings, restore verification |
| [API reference](docs/api.md) | Authentication, permissions, routes, and automation |
| [OpenAPI specification](openapi.yaml) | Machine-readable route and field coverage |
| [Troubleshooting](docs/troubleshooting.md) | Startup, worker, mount, and recovery checks |
| [Developer guide](docs/developer-guide.md) | Local development and tests |

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Run Python tests and the frontend build before opening a pull request.

```bash
source venv/bin/activate
python -m pytest tests/ -q
npm ci
npm run build
```

## Disclaimer

Review scan and cleanup results before acting. PixelProbe records observations about files and inventory. It cannot prove that a missing path was deliberately deleted rather than temporarily unreachable.

## License

MIT. See [LICENSE](LICENSE). Copyright is held by PixelProbe contributors.

## LLM disclosure

This project was developed using AI agents as a pair programmer. It was NOT vibe coded. For context, I'm a systems engineer who also writes code professionally with 15+ years of experience. The codebase follows engineering best practices, and all architecture and design decisions were made by me, not by AI. All code generated by LLMs was reviewed and tested by me, a human.
