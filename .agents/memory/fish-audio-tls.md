---
name: Replit outbound TLS trust
description: HTTPX certificate validation for Fish Audio requests in this Replit runtime.
---

Use `ssl.create_default_context()` for Fish Audio HTTPX clients in this runtime. The default HTTPX/certifi trust bundle failed to validate the outbound certificate chain, while Python's system CA context succeeded.

**Why:** The observed failure was `CERTIFICATE_VERIFY_FAILED` with the Replit runtime's local issuer; disabling TLS verification would expose API credentials and traffic.

**How to apply:** Keep certificate verification enabled and pass the system SSL context to Fish Audio clients. For diagnosis, use an unauthenticated HTTPS request; never test by generating speech or exposing API keys.
