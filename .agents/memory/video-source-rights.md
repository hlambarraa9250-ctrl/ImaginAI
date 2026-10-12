---
name: Video-source rights handling
description: Project rules for source metadata, license confidence, user review, and optional providers.
---

Treat a media asset as rights-confirmed only when the source provides a known license that the app has explicitly verified. Unknown or ambiguous terms must remain `needs_review`; do not infer public-domain status from a government or archive source. Keep provider, asset identity, source page, license, and attribution metadata with the asset. Distinguish authentic historical material from illustrative stock, and do not invent dimensions, duration, or license terms.

Coverr is optional and disabled by default. Storing a Coverr key must not enable the provider.

**Why:** Users need to review unclear rights before exporting, and enabling an optional source should require a deliberate configuration change rather than happen as a side effect of adding credentials.

**How to apply:** Apply these rules to every new footage adapter and to review/export behavior. Use `needs_review` unless authoritative license terms can be confirmed; require user acknowledgement before export. Keep Coverr opt-in.
