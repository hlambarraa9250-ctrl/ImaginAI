---
name: Device-scoped API identity
description: Durable rule for keeping device-owned jobs accessible across proxied browser requests.
---

Every device-scoped job request, including status polling, must send the same client device identifier in headers; the server should synchronize that identifier into the cookie when it receives a header.

**Why:** Proxied browser requests can omit or carry a stale cookie. If polling relies on the cookie alone, a valid job is treated as belonging to another device and returns 404.

**How to apply:** Use one browser-stored device identifier for create, status, review, resume, project, and settings requests, while retaining server-side ownership checks.