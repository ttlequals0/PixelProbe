# Contributing to PixelProbe

## Before you start

Use a feature or fix branch based on `main`. Keep credentials out of the repository. Store deployment secrets in an external, untracked secret source.

For larger changes, open an issue first. Security findings should not include exploit details in a public issue; contact the maintainers privately.

## Development

```bash
python3.12 -m venv venv
source venv/bin/activate
python -m pip install -r requirements-test.txt
python -m pytest tests/ -v
npm ci
npm run test:dom
npm run build
```

Python 3.12 is the supported development and production runtime.

PostgreSQL is required for production behavior. Some focused tests use SQLite fixtures, so use the documented PostgreSQL integration variables for locking, startup, and worker behavior.

Frontend builds use Node 22.22.2.

Frontend changes must keep stored values in text nodes or DOM properties and must not add inline event handlers. Run the DOM regression test when changing renderers.

## Pull requests

- Explain why the change is needed and what it changes.
- Include tests for security, lifecycle, and data-integrity behavior.
- State what was verified and what remains unverified.
- Update `CHANGELOG.MD` for user-visible fixes and operational changes.
- Do not include private domains, access tokens, or environment-specific deployment details.

The project uses a planning, review, and implementation workflow. Planning and review are separate from coding work. A review pass is required before merge.
