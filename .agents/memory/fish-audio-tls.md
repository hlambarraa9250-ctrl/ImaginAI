---
name: Replit outbound HTTPX TLS trust
description: Use Python's system CA context for outbound HTTPX requests in this Replit runtime.
---

Use `ssl.create_default_context()` for outbound HTTPX clients in this runtime, including footage providers and health checks. The default HTTPX/certifi trust bundle failed to validate the Replit outbound certificate chain, while Python's system CA context succeeded.

**Why:** Multiple external services failed with `CERTIFICATE_VERIFY_FAILED` and Replit's local issuer, while system-trust requests succeeded. Disabling TLS verification would expose API credentials and traffic.

**How to apply:** Keep certificate verification enabled and pass a system SSL context to HTTPX clients that call external services. For diagnosis, use unauthenticated HTTPS requests where possible; never expose API keys.
