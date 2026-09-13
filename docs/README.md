# PixelProbe Documentation

Full documentation for PixelProbe. Start with the [project README](../README.md) for a quick install, then come here for the details.

## Contents

### Running PixelProbe

- [Installation](installation.md) - requirements, Docker quick start, manual install, first-run setup
- [Docker Setup](docker-setup.md) - root Compose configuration, container roles, PostgreSQL tuning, and the 15-to-18 migration guide
- [Web Interface](web-interface.md) - dashboard, file actions, admin views, screenshots
- [Configuration](configuration.md) - every environment variable with its real default, notification providers, schedules, exclusions
- [Performance Tuning](performance-tuning.md) - concurrency knobs, chunk sizing, worker recycling, CPU sizing for video scanning
- [Troubleshooting](troubleshooting.md) - symptom-driven recipes, stuck-scan revival and recovery, incomplete scan repair
- [Operational Evidence](operational-evidence.md) - lifecycle limits, report history, recovery, restore verification, and deployment checks

### Understanding PixelProbe

- [How It Works](how-it-works.md) - layers, containers, the scan lifecycle from claim to finalize, the validation pipeline, failure recovery
- [Scan Types](scan-types.md) - the scan types, what each one checks, and when to use which
- [Project Structure](project-structure.md) - where everything lives in the repository
- [Glossary](glossary.md) - every term the app uses, defined and linked to the doc that covers it

### Integrating with PixelProbe

- [API Reference](api.md) - authentication, every endpoint, rate limits, response shapes
- [OpenAPI Specification](../openapi.yaml) - machine-readable route and method coverage; some response fields remain partial
- [Integration Guide](examples/integration-guide.md) - polling patterns, CI hooks, notification payloads
- Client examples: [Python](examples/python-client.py), [Node.js](examples/nodejs-client.js), [Bash](examples/bash-client.sh)

### Developing PixelProbe

- [Developer Guide](developer-guide.md) - local setup, code layout, conventions, contribution flow
- [Database Schema](database-schema.md) - durable contracts, current security fields, indexes, and startup migration notes
- [Testing Guide](testing-guide.md) - test layout, markers, fixtures, what CI actually runs
- [Release Process](release-process.md) - version bump to deployed container, including the scripted GitHub release
- [Tools and Scripts](tools-and-scripts.md) - every maintenance tool and helper script, with the dry-run conventions

### Screenshots

- [UI Screenshots](screenshots/) - visual guide to the interface

[< Project README](../README.md)
