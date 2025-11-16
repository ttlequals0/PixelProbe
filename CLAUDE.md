  Core Principles

  - NEVER create mock data or simplified components unless explicitly told to do so
  - NEVER replace existing complex components with simplified versions - always fix the actual problem
  - ALWAYS work with the existing codebase - do not create new simplified alternatives
  - ALWAYS find and fix the root cause of issues instead of creating workarounds
  - NEVER remove failed tests, ALWAYS fix them
  - NEVER skip tests - always wait for them to finish and fix any failures
  - NEVER just agree - always state your reasons for choices made. We are a TEAM

  Change Management

  - ALWAYS track all changes in CHANGELOG.md
  - ALWAYS refer to CHANGELOG.md when working on tasks
  - Nuild new changes into a feature / fix branch off oof master
  - NEVER commit directly to main or master branches

  Testing Requirements

  - Run ALL tests after code changes
  - NEVER skip failed tests, ALWAYS fix the problem
  - Python testing should use venv
  - Always run test suite before each build and make sure the app will actually start
  - Always make sure the App builds successfully

  Docker Guidelines

  - Build for platform="linux/amd64"
  - Docker Hub user and org are ttlequals0
  - Always check what the next version should be before tagging (from Docker Hub and CHANGELOG.md)
  - Tag format: latest and version matching '/Users/dkrachtus/repos/PixelProbe/version.py'
  - Do NOT tag with major.minor only (e.g., avoid ttlequals0/pixelprobe:2.0)
  - If testing locally, cleanup afterwards

  Live Server Debugging

  - Always look at live server logs before making changes
  - Server: https://pixelprobe.ttlequals0.com/
  - Verify app version from server
  - Use Portainer API to access container logs:
  curl -X GET "https://portainer.ttlequals0.com/api/endpoints/2/docker/containers/<CONTAINER_ID>/logs?stdout=true&stderr=true" \
    -H "Authorization: Bearer $BEARER" \
    --output -

  Version Management

  - Always update version.py with changes

  Security & Quality

  - Always scrub out all sensitive data in the repo
  - NO emojis in any code or documentation
  - Don't add yourself or Claude to git commits

  Workspace

  - Only work in /Users/dkrachtus/repos/PixelProbe