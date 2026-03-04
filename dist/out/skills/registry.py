# skills/registry.py — Two-tier skill registry (v5.0)
#
# Global and Restricted skills by name; register_skill, get_skill, list_skills.
# See EXECUTION_LAYER_REFACTOR_PLAN.md.

from __future__ import annotations

from typing import Any, Dict, List, Optional

from skills.base import AccessLevel, BaseSkill

_SKILL_REGISTRY: Dict[str, BaseSkill] = {}


def register_skill(skill: BaseSkill) -> None:
    """Register a skill by name (case-insensitive key). Overwrites existing."""
    key = (skill.name or "").strip().upper()
    if not key:
        raise ValueError("Skill name cannot be empty")
    _SKILL_REGISTRY[key] = skill


def get_skill(name: str) -> Optional[BaseSkill]:
    """Look up skill by name (case-insensitive)."""
    return _SKILL_REGISTRY.get((name or "").strip().upper())


def list_skills() -> List[Dict[str, Any]]:
    """Return metadata for all registered skills (name, description, version, access_level)."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "version": s.version,
            "access_level": s.access_level.value,
            "idempotent": s.idempotent,
        }
        for s in _SKILL_REGISTRY.values()
    ]


def clear_registry() -> None:
    """Clear all skills (for tests only)."""
    _SKILL_REGISTRY.clear()
