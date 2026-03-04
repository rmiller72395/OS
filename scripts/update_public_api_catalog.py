#!/usr/bin/env python3
# scripts/update_public_api_catalog.py — Optional: fetch public API list from GitHub and update local catalog (v5.0)
#
# Run on demand by owner. Fetches README from public-api-lists/public-api-lists and parses entries best-effort.
# Does NOT auto-add domains to allowlist. Does NOT auto-enable tools. Catalog is for discovery only.
# Usage: python scripts/update_public_api_catalog.py
# Output: data/catalog/public_api_catalog.json (merged with existing seed)

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CATALOG_URL = "https://raw.githubusercontent.com/public-api-lists/public-api-lists/main/README.md"
OUT_PATH = ROOT / "data" / "catalog" / "public_api_catalog.json"


def load_existing() -> list:
    if not OUT_PATH.exists():
        return []
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        return data.get("apis", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    except Exception:
        return []


def fetch_readme() -> str:
    try:
        import urllib.request
        req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "Sovereign/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        return ""


def parse_readme_entries(text: str) -> list:
    """Best-effort parse README for API name, URL, description."""
    entries = []
    # Look for markdown links and list items
    for line in text.splitlines():
        line = line.strip()
        # Pattern: - [Name](url) - description or * [Name](url)
        m = re.match(r"^[-*]\s*\[([^\]]+)\]\(([^)]+)\)\s*[-–—]?\s*(.*)$", line)
        if m:
            name, url, desc = m.groups()
            url = url.strip()
            if url.startswith("http://") or url.startswith("https://"):
                try:
                    from urllib.parse import urlparse
                    p = urlparse(url)
                    base_url = f"{p.scheme}://{p.netloc}" if p.netloc else url
                except Exception:
                    base_url = url
                entries.append({
                    "name": name.strip()[:200],
                    "description": (desc or "").strip()[:500],
                    "base_url": base_url,
                    "docs_url": url,
                    "auth": "unknown",
                    "category": "general",
                })
    return entries


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing()
    existing_names = {e.get("name") for e in existing if isinstance(e, dict) and e.get("name")}
    readme = fetch_readme()
    if not readme:
        print("No new data; keeping existing catalog.", file=sys.stderr)
        merged = existing
    else:
        new_entries = parse_readme_entries(readme)
        for e in new_entries:
            if isinstance(e, dict) and e.get("name") and e["name"] not in existing_names:
                existing.append(e)
                existing_names.add(e["name"])
        merged = existing[:200]  # cap size
    out_data = {"apis": merged, "source": "sovereign_catalog", "count": len(merged)}
    OUT_PATH.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(merged)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
