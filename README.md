<div align="center">
  <img src="static/images/pixelprobe-logo.svg" alt="PixelProbe" width="200" height="200">
</div>

PixelProbe is a self-hosted server that finds corrupted video, image, and audio files across your media libraries before you hit play. It validates every file with FFmpeg, ImageMagick, and PIL, watches for bitrot with a rolling integrity queue, and gives you a web UI for browsing results, scheduling scans, and clearing false positives. Production-tested against libraries of over a million files.

## Features

**Detection**
- Deep FFmpeg video analysis with a staged pipeline: full-stream validation, frame integrity, temporal sampling, freeze detection with black-frame false-positive filtering
- Image validation through both PIL/Pillow (HEIC via pillow-heif) and ImageMagick pixel decode; audio validation through FFmpeg
- Separate warning verdict for signals that do not prove damage, so real corruption stays visible
- Bitrot detection: a content hash change without a matching mtime change flags the file for review instead of silently adopting the new hash
- Ignored-error patterns to suppress known benign decoder noise per deployment

**Scanning**
- Chunk-distributed parallel scanning on Celery workers, with heartbeats and automatic revival after container restarts
- Rolling integrity queue that sweeps the library stalest-first under optional per-run time budgets
- Scheduled scans on cron expressions, path and extension exclusions, bulk rescans
- Real-time progress with ETA and phase tracking; scans of 50GB+ remux files and million-file libraries are routine

**Interface and ops**
- Responsive web UI with dark/light themes, mobile layout, in-browser media preview, and bulk actions
- Trend analytics, scan reports with PDF/JSON export, in-app log viewer
- Event notifications to email (SMTP), Pushover, ntfy, or webhooks, one rule per event
- Healthchecks.io integration, full REST API with OpenAPI spec

**Security**
- Multi-user with role-based access, bcrypt passwords, API tokens, CSRF-protected sessions, audit logging
- First-run setup wizard creates the admin account

## How it works

1. **Discovery** - a directory walk registers candidate files by extension
2. **Chunking** - pending files are split into path-range chunks and queued to Celery workers
3. **Validation** - each file runs the FFmpeg/ImageMagick/PIL pipeline; verdicts are healthy, warning, corrupted, or error
4. **Integrity** - previously scanned files are re-hashed on a rolling queue to catch silent changes (bitrot)
5. **Review** - the web UI surfaces verdicts, trends, and false-positive tools (Mark as Good, ignored patterns)

Full pipeline detail (validation stages, chunk lifecycle, revival, failure recovery) is in [docs/how-it-works.md](docs/how-it-works.md).

## Requirements

- Docker with Docker Compose (the stack runs web, Celery worker, PostgreSQL, and Valkey containers)
- PostgreSQL is required; the bundled compose provides it
- 4 CPU cores and 8 GB RAM recommended for video-heavy libraries; see [docs/installation.md](docs/installation.md)

**Upgrading to v2.7.0+**: the bundled compose defaults to PostgreSQL 18 and Valkey 9. An existing PostgreSQL 15 data volume will not start on the 18 image - follow the [migration guide](docs/docker-setup.md#postgresql-15-to-18-migration-required-for-v270) first, or pin `postgres:15-alpine`.

## Quick start

```bash
# 1. Create environment file
cat > .env << EOF
SECRET_KEY=long-random-string
POSTGRES_PASSWORD=another-long-random-string
MEDIA_PATH=/path/to/your/media
EOF

# 2. Run
docker compose up -d
```

Open `http://localhost:5000`, create the admin account through the first-run wizard, and start a scan. Media is mounted read-only; `SCAN_PATHS` defaults to `/media`.

Everything else (concurrency, schedules, exclusions, notifications) is configured through the web UI or environment variables - see [docs/configuration.md](docs/configuration.md) and `.env.example`.

Images are published to Docker Hub as `ttlequals0/pixelprobe` (`:latest` plus one tag per version).

## Documentation

| Topic | |
|---|---|
| [How It Works](docs/how-it-works.md) | Layers, containers, scan lifecycle from claim to finalize, validation pipeline, failure recovery |
| [Installation](docs/installation.md) | Requirements, Docker quick start, manual install, first-run setup |
| [Docker Setup](docs/docker-setup.md) | Full compose stack, container roles, PostgreSQL tuning, the 15-to-18 migration |
| [Web Interface](docs/web-interface.md) | Dashboard, file actions, admin views, screenshots |
| [Configuration](docs/configuration.md) | Every environment variable with its real default, notifications, schedules, exclusions |
| [Performance Tuning](docs/performance-tuning.md) | Concurrency knobs, chunk sizing, worker recycling, CPU sizing for video scanning |
| [Scan Types](docs/scan-types.md) | The scan types, what each checks, and when to use which |
| [API Reference](docs/api.md) | Authentication, every endpoint, rate limits, response shapes |
| [Integration Guide](docs/examples/integration-guide.md) | Polling patterns, CI hooks, notification payloads, client examples |
| [Troubleshooting](docs/troubleshooting.md) | Symptom-driven recipes, stuck-scan revival, incomplete scan repair |
| [Glossary](docs/glossary.md) | Every term the app uses, linked to the doc that covers it |
| [Project Structure](docs/project-structure.md) | Where everything lives in the repository |
| [Database Schema](docs/database-schema.md) | All 17 models, indexes, the startup migration pattern |
| [Developer Guide](docs/developer-guide.md) | Local setup, conventions, contribution flow |
| [Testing Guide](docs/testing-guide.md) | Test layout, markers, fixtures, what CI runs |
| [Release Process](docs/release-process.md) | Version bump to deployed container, scripted GitHub releases |
| [Tools and Scripts](docs/tools-and-scripts.md) | Maintenance tools and helper scripts, dry-run conventions |

Or browse the [full docs index](docs/README.md).

## Supported file formats

Video (MP4, MKV, AVI, MOV, WebM, HEVC, ProRes, MXF, AVCHD, and more), images (JPEG, PNG, GIF, TIFF, WebP, HEIC, and most camera RAW formats), and audio (MP3, AAC, FLAC, WAV, Opus, DSD, AC3, DTS). The canonical extension lists live in [pixelprobe/constants.py](pixelprobe/constants.py).

## License

MIT - see the LICENSE file.

## Acknowledgments

- [FFmpeg](https://ffmpeg.org/) for video analysis
- [ImageMagick](https://imagemagick.org/) for image processing
- [PIL/Pillow](https://pillow.readthedocs.io/) for Python image handling

## Support

For issues, questions, or contributions, visit the [GitHub repository](https://github.com/ttlequals0/PixelProbe/issues).

## LLM disclosure

This project was developed using AI agents as a pair programmer. It was NOT vibe coded. For context, I'm a systems engineer who also writes code professionally with 15+ years of experience. The codebase follows engineering best practices, and all architecture and design decisions were made by me, not by AI. All code generated by LLMs was reviewed and tested by me, a human.
