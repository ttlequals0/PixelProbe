# CLAUDE.md

## CRITICAL INSTRUCTIONS -- ALWAYS READ THIS BEFORE EVERY CHANGE!!!
- **NEVER create mock data or simplified components** unless explicitly told to do so
- **NEVER replace existing complex components with simplified versions** - always fix the actual problem
- **ALWAYS work with the existing codebase** - do not create new simplified alternatives
- **ALWAYS find and fix the root cause** of issues instead of creating workarounds
- **ALWAYS Track all changes in CHANGELOG.MD**
- **ALWAYS refer to CHANGELOG.MD when working on tasks**
- **Always make sure the App builds successfully**
- **Always scrub out all sensitive data in the repo**
- ALWAYS run all tests after code changes.
- When debugging issues, focus on fixing the existing implementation, not replacing it
- When something doesn't work, debug and fix it - don't start over with a simple version
- Always look at live server logs before making changes.
  - Server: https://pixelprobe.ttlequals0.com/
  - Verify app version from server
  - use portainer API to access container loges
  - example ```curl -X GET "https://portainer.ttlequals0.com/api/endpoints/2/docker/containers/<CONTAINER_ID>/logs?stdout=true&stderr=true" \
    -H "Authorization: Bearer $BEARER"
    --output -```
- docker should be built for platform="linux/amd64" 
- docker versioning should just be major.minor version ie 0.23
- docker always check what the next version should be before tagging, you can get this info from docker hub and should be tracked in CHANGELOG.md
- Always update version.py
- docker hub user and org are ttlequals0
- docker if you test locally cleanup afterwards
- python testing should use venv
- dont add yourself or claude to git commits
- Always follow claude.md

### Known Issues to Address
- **Table Text Overlap Issue**: The current CSS has fixed widths for table cells which might be causing text overlap on certain screen sizes. The CSS uses fixed width of 350px for file-path-cell which may not be responsive. This needs investigation by looking at the specific overlap issue in the browser and potentially implementing responsive table design.
