---
module_name: http_request
tools:
  - http_request
scopes:
  - read:*
  - write:*
verification_checklist:
  - Confirm URL is in allowlist
  - Check method is allowed
common_failure_modes:
  - Timeout on slow endpoints
  - SSL errors on self-signed certs
---

# HTTP Request Tool

Use for outbound HTTP calls. Respect allowlist and scopes.
