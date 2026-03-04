# skills/public_api_allowlist.py — Safe HTTP allowlist for read-only GET tools (v5.0)
#
# Resolves allowlist domains and HTTP limits from config (sovereign_config.json) or env.
# Used by http_get_json_readonly. Never auto-enables; owner must set allowlist.

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _config_path() -> Path:
    base = os.getenv("SOVEREIGN_DATA_DIR", os.getcwd())
    return Path(base) / "sovereign_config.json"


def get_allowlist_config() -> Dict[str, Any]:
    """Load config with allowlist and HTTP limits. Returns dict with defaults if file missing."""
    path = _config_path()
    out: Dict[str, Any] = {
        "public_api_allowlist_domains": [],
        "public_api_allowlist_url_prefixes": [],
        "http_max_bytes": 1_000_000,
        "http_max_redirects": 0,
        "http_default_timeout_s": 20,
        "http_max_timeout_s": 60,
    }
    env_domains = os.getenv("PUBLIC_API_ALLOWLIST_DOMAINS", "").strip()
    if env_domains:
        out["public_api_allowlist_domains"] = [d.strip().lower() for d in env_domains.split(",") if d.strip()]
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("public_api_allowlist_domains"), list):
                out["public_api_allowlist_domains"] = [str(d).strip().lower() for d in data["public_api_allowlist_domains"] if str(d).strip()]
            if isinstance(data.get("public_api_allowlist_url_prefixes"), list):
                out["public_api_allowlist_url_prefixes"] = [str(p).strip() for p in data["public_api_allowlist_url_prefixes"] if str(p).strip()]
            if isinstance(data.get("http_max_bytes"), (int, float)):
                out["http_max_bytes"] = int(data["http_max_bytes"])
            if isinstance(data.get("http_max_redirects"), (int, float)):
                out["http_max_redirects"] = int(data["http_max_redirects"])
            if isinstance(data.get("http_default_timeout_s"), (int, float)):
                out["http_default_timeout_s"] = int(data["http_default_timeout_s"])
            if isinstance(data.get("http_max_timeout_s"), (int, float)):
                out["http_max_timeout_s"] = int(data["http_max_timeout_s"])
        except Exception:
            pass
    return out


def get_allowlist_domains() -> List[str]:
    """Return list of allowed hostnames (lowercase). No IP literals, no wildcards."""
    cfg = get_allowlist_config()
    domains = cfg.get("public_api_allowlist_domains") or []
    return [d for d in domains if isinstance(d, str) and d and not _is_ip_literal(d)]


def get_allowlist_url_prefixes() -> List[str]:
    """Return optional URL prefixes (e.g. https://api.example.com/v1/)."""
    cfg = get_allowlist_config()
    return cfg.get("public_api_allowlist_url_prefixes") or []


def _is_ip_literal(host: str) -> bool:
    """True if host looks like an IP address (v4 or v6)."""
    host = (host or "").strip()
    if not host:
        return True
    # IPv4
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return True
    # IPv6 bracket or plain
    if host.startswith("[") or ":" in host:
        return True
    return False


def is_url_allowed(url: str, allowed_domains: Optional[List[str]] = None, allowed_prefixes: Optional[List[str]] = None) -> tuple[bool, str]:
    """
    Check if URL is allowed. Returns (allowed, reason).
    Enforces: host in allowed_domains (or URL in allowed_prefixes), no localhost, no IP, no file/ftp.
    """
    from urllib.parse import urlparse
    if not url or not isinstance(url, str):
        return False, "url missing or invalid"
    url = url.strip()
    if len(url) > 2048:
        return False, "url too long"
    if not url.startswith(("http://", "https://")):
        return False, "only http/https allowed"
    try:
        p = urlparse(url)
        host = (p.hostname or "").strip().lower()
        if not host:
            return False, "invalid host"
        if host in ("localhost", "localhost.", "127.0.0.1", "::1"):
            return False, "localhost not allowed"
        if _is_ip_literal(host):
            return False, "IP literals not allowed"
        domains = allowed_domains if allowed_domains is not None else get_allowlist_domains()
        prefixes = allowed_prefixes if allowed_prefixes is not None else get_allowlist_url_prefixes()
        if prefixes:
            for prefix in prefixes:
                if url.startswith(prefix.rstrip("/")) or url.startswith(prefix):
                    return True, ""
        if host in domains:
            return True, ""
        # Allow subdomains of listed domains (e.g. api.example.com when example.com is listed)
        for d in domains:
            if host == d or host.endswith("." + d):
                return True, ""
        return False, f"domain {host!r} not in allowlist"
    except Exception as e:
        return False, f"url parse error: {e}"
