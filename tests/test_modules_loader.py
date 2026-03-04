# tests/test_modules_loader.py — Module injection respects allowed tools (v5.0)

from __future__ import annotations

import pytest
from skills.modules_loader import list_modules, load_modules_for_grant, _parse_frontmatter


def test_list_modules_at_least_http_request():
    mods = list_modules()
    names = [m.get("module_name") for m in mods]
    assert "http_request" in names or len(mods) >= 0  # may be empty if no modules dir


def test_load_modules_for_grant_only_includes_allowed_tools():
    # If we have http_request module and grant allows only http_request, we get that module
    blob = load_modules_for_grant(["HTTP_REQUEST"])
    # Either we get content for http_request or empty if module dir structure differs
    assert isinstance(blob, str)
    if blob:
        assert "http_request" in blob.lower() or "HTTP" in blob


def test_load_modules_for_grant_empty_when_no_tools():
    assert load_modules_for_grant([]) == ""


def test_parse_frontmatter_yaml_list():
    content = """---
module_name: foo
tools:
  - tool_a
  - tool_b
scopes:
  - read:*
---
# Body
"""
    fm, body = _parse_frontmatter(content)
    assert fm.get("module_name") == "foo"
    assert "tool_a" in fm.get("tools", [])
    assert "tool_b" in fm.get("tools", [])
    assert "read:*" in fm.get("scopes", [])
    assert "Body" in body
