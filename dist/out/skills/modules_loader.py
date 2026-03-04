# skills/modules_loader.py — Load skill modules for worker prompts (v5.0)
#
# Only inject modules whose tools are in the grant's allowed_tools.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

_MODULES_DIR = Path(__file__).resolve().parent / "modules"


def _parse_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """Return (frontmatter dict, body). No frontmatter => ({}, content). Minimal YAML-like parse."""
    content = content.lstrip()
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end < 0:
        return {}, content
    block = content[3:end].strip()
    fm: Dict[str, Any] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if ":" not in stripped:
            i += 1
            continue
        k, _, v = stripped.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "module_name":
            fm["module_name"] = v.strip("'\"").strip()
        elif k == "tools":
            if v.startswith("["):
                fm["tools"] = [x.strip().strip("'\"").strip() for x in v[1:-1].split(",") if x.strip()]
            elif v:
                fm["tools"] = [v.strip("'\"")]
            else:
                # YAML list style: next lines "  - x"
                items = []
                i += 1
                while i < len(lines) and lines[i].strip().startswith("-"):
                    items.append(lines[i].strip()[1:].strip().strip("'\""))
                    i += 1
                fm["tools"] = items
                i -= 1
        elif k == "scopes":
            if v.startswith("["):
                fm["scopes"] = [x.strip().strip("'\"").strip() for x in v[1:-1].split(",") if x.strip()]
            elif v:
                fm["scopes"] = [v.strip("'\"")]
            else:
                items = []
                i += 1
                while i < len(lines) and lines[i].strip().startswith("-"):
                    items.append(lines[i].strip()[1:].strip().strip("'\""))
                    i += 1
                fm["scopes"] = items
                i -= 1
        elif k == "verification_checklist":
            fm["verification_checklist"] = v
        elif k == "common_failure_modes":
            fm["common_failure_modes"] = v
        i += 1
    body = content[end + 3:].lstrip()
    return fm, body


def list_modules() -> List[Dict[str, Any]]:
    """List all modules (name, tools, scopes from frontmatter)."""
    result: List[Dict[str, Any]] = []
    if not _MODULES_DIR.exists():
        return result
    for path in _MODULES_DIR.glob("*.md"):
        if path.name.startswith("README"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            fm, _ = _parse_frontmatter(text)
            result.append({
                "module_name": fm.get("module_name", path.stem),
                "tools": fm.get("tools") or [],
                "scopes": fm.get("scopes") or [],
                "path": str(path),
            })
        except Exception as e:
            logger.warning("modules_loader: skip %s: %s", path, e)
    return result


def load_modules_for_grant(allowed_tools: List[str]) -> str:
    """
    Load module content for modules whose tools are in allowed_tools.
    Returns a single text blob to inject into worker prompts.
    """
    allowed = set((t or "").strip().upper() for t in allowed_tools if t)
    if not allowed:
        return ""
    parts: List[str] = []
    for meta in list_modules():
        module_tools = [str(t).strip().upper() for t in (meta.get("tools") or [])]
        if not any(t in allowed for t in module_tools):
            continue
        path = meta.get("path")
        if not path:
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(text)
            name = fm.get("module_name", Path(path).stem)
            parts.append(f"## Module: {name}\n\n{body.strip()}\n")
        except Exception as e:
            logger.warning("modules_loader: load %s: %s", path, e)
    return "\n".join(parts) if parts else ""
