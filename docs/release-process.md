# Release process

This document describes the current process for creating and deploying a new
PixelProbe release. `$VERSION` below is the new semantic version (for
example `2.8.0`).

## Security note

This is a public repository. Never include environment-specific information
in documentation, PRs, commit messages, or release notes: no real domains,
server URLs, API tokens, webhook UUIDs, or internal hostnames.

## Pre-release checklist

- [ ] All planned changes are complete on a feature/fix branch
- [ ] CHANGELOG.MD has an entry for `$VERSION`
- [ ] No sensitive data in the repository

## Release steps

### 1. Bump the version and changelog (on a branch)

All work happens on a feature or fix branch off `main`; never commit to
`main` directly.

Update `_DEFAULT_VERSION` in `pixelprobe/version.py`:

```python
_DEFAULT_VERSION = '$VERSION'
```

Note: `__version__` in that file reads the `APP_VERSION` environment
variable with `_DEFAULT_VERSION` as the fallback, so `_DEFAULT_VERSION` is
the value to change.

Add the new section to CHANGELOG.MD:

```markdown
## [$VERSION] - YYYY-MM-DD

### Added
- ...

### Fixed
- ...

### Changed
- ...
```

### 2. Run tests locally

```bash
source venv/bin/activate
pytest -m "not real_media"
npm run build
```

Both must pass before pushing.

### 3. Push the branch and wait for CI and CodeQL

```bash
git add -A
git commit -m "Describe the release changes"
git push -u origin your-branch-name
```

Open a PR and wait for BOTH the CI workflow and CodeQL to pass before
building any images. CodeQL runs via GitHub's default setup (there is no
`codeql.yml` workflow file), so check the PR checks list, not the workflows
directory. Fixing a CodeQL finding after the image is built forces a
rebuild and re-push of the same tag, and re-pushing an unchanged tag will
not trigger a container recreate on deploy.

### 4. Build and smoke-test the Docker image

```bash
docker build --platform=linux/amd64 -t ttlequals0/pixelprobe:$VERSION .
docker tag ttlequals0/pixelprobe:$VERSION ttlequals0/pixelprobe:latest
```

NEVER tag with major.minor only (for example `2.8`); use the full
`$VERSION` and `latest`.

A bare `docker run` cannot start the container - the app requires
PostgreSQL and Redis/Valkey. Smoke test with the compose stack from
[docker-setup.md](docker-setup.md), pointing the pixelprobe service at the
freshly built tag, then:

```bash
# Liveness: /healthz is unauthenticated; /health requires auth
curl -s http://localhost:5000/healthz
```

Expected: `{"status": "ok", "version": "$VERSION"}`. Clean up the local
test containers when done:

```bash
docker compose down -v
```

### 5. Push to Docker Hub

```bash
docker login -u ttlequals0
docker push ttlequals0/pixelprobe:$VERSION
docker push ttlequals0/pixelprobe:latest
```

### 6. Merge and update main

Merge the PR (squash), then:

```bash
git checkout main
git pull
```

### 7. Publish the GitHub release

```bash
scripts/publish_release.sh $VERSION            # or add --dry-run to preview
```

The script enforces its own guards before creating anything:

- Running on `main` with a clean working tree
- `HEAD` equals `origin/main`
- `pixelprobe/version.py` matches `$VERSION`
- Tag `v$VERSION` does not already exist
- `ttlequals0/pixelprobe:$VERSION` is present on Docker Hub

Release notes are extracted from CHANGELOG.MD by
`scripts/changelog_section.py`, rolled up from every section since the
previous `v*` tag (`--rollup-since`), so a branch that bumped the version
more than once publishes all of its sections. The script then creates the
annotated tag `v$VERSION` and the GitHub release.

### 8. Deploy

Trigger the Portainer stack webhook (the URL lives outside the repository;
never commit the real UUID or hostname):

```bash
curl -s -X POST "$PORTAINER_WEBHOOK_URL?BUILD_VERSION=$VERSION"
```

- The container takes about 30 seconds to restart
- Verify with `GET /api/version` (authenticated) that the running version
  is `$VERSION`
- Re-pushing an unchanged tag will NOT trigger a recreate: Portainer only
  recreates containers when `BUILD_VERSION` changes

### 9. Rollback

If issues are discovered after deploy:

```bash
docker pull ttlequals0/pixelprobe:$PREVIOUS_VERSION
docker tag ttlequals0/pixelprobe:$PREVIOUS_VERSION ttlequals0/pixelprobe:latest
docker push ttlequals0/pixelprobe:latest
curl -s -X POST "$PORTAINER_WEBHOOK_URL?BUILD_VERSION=$PREVIOUS_VERSION"
```

Mark the bad version in CHANGELOG.MD with the `[YANKED]` convention:

```markdown
## [$VERSION] - YYYY-MM-DD [YANKED]
```

## Version numbering

PixelProbe follows semantic versioning:
- Major (X.0.0): breaking changes, major features
- Minor (x.X.0): new features, minor changes
- Patch (x.y.X): bug fixes, small improvements
