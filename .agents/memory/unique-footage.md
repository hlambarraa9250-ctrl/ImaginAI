---
name: Unique footage per video
description: The requirement that footage assets do not repeat within a generated video.
---

Do not use the same source footage asset more than once in a generated video. If a job contains duplicates, replace them with distinct sources and require a fresh review before final rendering; never silently export repeated footage.

**Why:** The user explicitly stated that a scene or shot must appear only once in the video.

**How to apply:** Track stable provider asset IDs and canonical source-page URLs across all shots, search additional result pages when needed, and fail clearly rather than falling back to a used asset.
