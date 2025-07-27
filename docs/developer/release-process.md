# Release Process

This document outlines the process for creating and deploying a new release of PixelProbe.

## Pre-Release Checklist

Before starting the release process, ensure:

- [ ] All planned features are complete and merged
- [ ] All tests are passing in CI/CD
- [ ] Documentation is up to date
- [ ] CHANGELOG.md is updated with all changes
- [ ] No sensitive data in the repository

## Release Steps

### 1. Update Version Number

Update the version in `pixelprobe/version.py`:
```python
__version__ = "2.1.0"  # New version number
```

### 2. Update CHANGELOG

Add a new entry to CHANGELOG.md:
```markdown
## [2.1.0] - 2025-07-27

### Added
- Feature descriptions...

### Fixed
- Bug fix descriptions...

### Changed
- Change descriptions...
```

### 3. Update README

Update version references in README.md:
- Version badge/text at the top
- Docker image versions section
- Any version-specific documentation

### 4. Run Tests

Ensure all tests pass:
```bash
# Run test suite
pytest

# Run with coverage
pytest --cov=pixelprobe --cov-report=term

# Check for any warnings
pytest -W error
```

### 5. Build Docker Image

Build the Docker image for linux/amd64:
```bash
# Build for specific platform
docker build --platform linux/amd64 -t ttlequals0/pixelprobe:2.1.0 .

# Also tag as latest
docker tag ttlequals0/pixelprobe:2.1.0 ttlequals0/pixelprobe:latest
```

### 6. Test Docker Image Locally

```bash
# Run the built image
docker run -d \
  -p 5001:5000 \
  -e SECRET_KEY=test-key \
  -e SCAN_PATHS=/media \
  -v /tmp/test-media:/media \
  ttlequals0/pixelprobe:2.1.0

# Check it's running
docker ps

# Test the API
curl http://localhost:5001/health

# Stop and remove
docker stop $(docker ps -q --filter ancestor=ttlequals0/pixelprobe:2.1.0)
docker rm $(docker ps -aq --filter ancestor=ttlequals0/pixelprobe:2.1.0)
```

### 7. Push to Docker Hub

```bash
# Login to Docker Hub
docker login -u ttlequals0

# Push both tags
docker push ttlequals0/pixelprobe:2.1.0
docker push ttlequals0/pixelprobe:latest
```

### 8. Create Git Branch and Commit

```bash
# Create release branch
git checkout -b 2.1.0

# Add all changes
git add -A

# Commit with descriptive message
git commit -m "Version 2.1.0: Major milestone release

- 80+ improvements since 2.0.53
- Added scan reports feature
- Enhanced security with input validation
- Modular architecture overhaul
- Comprehensive audio format support
- Performance optimizations
- Multiple bug fixes"

# Push to GitHub
git push -u origin 2.1.0
```

### 9. Create GitHub Release

1. Go to GitHub repository
2. Click "Releases" → "Create a new release"
3. Tag version: `v2.1.0`
4. Target: `2.1.0` branch
5. Release title: `Version 2.1.0 - Major Milestone Release`
6. Description: Copy key highlights from CHANGELOG
7. Attach any relevant assets
8. Publish release

### 10. Update Docker Hub Description

Update the Docker Hub repository description with:
- New version information
- Key features of the release
- Link to GitHub release

### 11. Post-Release

- [ ] Verify Docker Hub has the new image
- [ ] Test pulling and running the new image
- [ ] Update any deployment documentation
- [ ] Announce release (if applicable)

## Version Numbering

PixelProbe follows semantic versioning:
- **Major** (X.0.0): Breaking changes, major features
- **Minor** (2.X.0): New features, minor changes
- **Patch** (2.1.X): Bug fixes, small improvements

## Rollback Process

If issues are discovered:

1. **Docker Hub**: Re-tag previous version as latest
```bash
docker pull ttlequals0/pixelprobe:2.0.133
docker tag ttlequals0/pixelprobe:2.0.133 ttlequals0/pixelprobe:latest
docker push ttlequals0/pixelprobe:latest
```

2. **GitHub**: Create hotfix branch from previous version
3. **Communication**: Notify users of the issue

## Security Considerations

- Never commit sensitive data (keys, passwords, tokens)
- Review all changes for security implications
- Ensure no debug code remains
- Verify all dependencies are up to date
- Run security scanning tools if available

## Automation

Consider automating parts of this process:
- GitHub Actions for CI/CD
- Automated Docker builds
- Version bumping scripts
- Release note generation

## Notes

- Always test thoroughly before release
- Keep release notes clear and user-focused
- Maintain backward compatibility when possible
- Document any breaking changes clearly