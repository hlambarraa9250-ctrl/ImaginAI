---
name: Next.js workspace dependency layout
description: Environment-specific guidance for running this imported Next.js frontend with Replit's workspace package setup.
---

Turbopack must have its root set to the frontend directory and must resolve dependencies from a physical `frontend/node_modules` directory; a symlink to a parent workspace `node_modules` is rejected as outside the filesystem root.

**Why:** The workspace package installer can place Node dependencies at the repository root, while the imported app runs from `frontend`; Next.js otherwise infers the wrong root or panics on an out-of-root symlink.

**How to apply:** Preserve the explicit `turbopack.root` configuration and ensure the frontend has a local dependency tree when restarting or rebuilding the app.