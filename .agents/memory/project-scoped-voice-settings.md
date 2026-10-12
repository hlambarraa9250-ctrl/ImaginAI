---
name: Project-scoped Fish voice settings
description: The project-specific scope of renaming or removing Fish Audio voices.
---

Fish Audio voice renames and removals are overrides on the selected project only. Hiding a voice must not delete it from Fish Audio, from the device-wide voice library, or from any other project.

**Why:** The user explicitly requires that deleting a voice in one project leave it available in every other project.

**How to apply:** Store display-name and hidden-state overrides keyed by project and voice ID. Check project ownership on every update, and do not call Fish Audio deletion endpoints for project-level removals.
