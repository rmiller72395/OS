# model_routing.py — Centralized model routing (v5.0)
#
# Loads model_routing.json (or MODEL_ROUTING_PATH). Exposes resolve_model(layer, attempt_index)
# and validation for self-test. CFO-gated worker paid fallback enforced by caller.

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REQUIRED_LAYERS = ("CEO", "CFO", "CISO", "MANAGER", "WORKER_EXECUTION")
ALLOWED_PROVIDERS = frozenset({"local", "openai", "anthropic", "google"})

_ROUTING: Optional[Dict[str, Any]] = None
_ROUTING_PATH: Optional[str] = None


def _routing_path() -> str:
    path = os.getenv("MODEL_ROUTING_PATH", "").strip()
    if path:
        return path
    root = Path(__file__).resolve().parent
    for name in ("model_routing.json", "model_routing.yaml"):
        p = root / name
        if p.exists():
            return str(p)
    return str(root / "model_routing.json")


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_routing(path: Optional[str] = None) -> Dict[str, Any]:
    """Load routing config from path or env default. Caches in module state."""
    global _ROUTING, _ROUTING_PATH
    p = path or _routing_path()
    if _ROUTING is not None and _ROUTING_PATH == p:
        return _ROUTING
    if not os.path.isfile(p):
        raise FileNotFoundError(f"Model routing file not found: {p}")
    raw = _load_json(p) if p.lower().endswith(".json") else _load_yaml(p)
    _ROUTING = raw
    _ROUTING_PATH = p
    return _ROUTING


def _load_yaml(path: str) -> Dict[str, Any]:
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        raise ImportError("YAML routing file requires PyYAML; use model_routing.json or pip install pyyaml")


def validate_routing(routing: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str]]:
    """
    Validate routing schema. Returns (ok, list of error messages).
    Checks: layers present, provider/model shape, providers in allowlist,
    WORKER_EXECUTION has cfo_gated_paid_fallback if it has paid fallbacks.
    """
    errors: List[str] = []
    data = routing if routing is not None else (load_routing() if _ROUTING is not None else None)
    if data is None:
        try:
            data = load_routing()
        except Exception as e:
            return False, [f"Load failed: {e}"]

    layers = data.get("layers")
    if not isinstance(layers, dict):
        errors.append("Missing or invalid 'layers' object")
        return len(errors) == 0, errors

    for layer in REQUIRED_LAYERS:
        if layer not in layers:
            errors.append(f"Missing required layer: {layer}")

    for layer_name, cfg in layers.items():
        if not isinstance(cfg, dict):
            errors.append(f"Layer '{layer_name}' must be an object")
            continue
        if "provider" not in cfg or "model" not in cfg:
            errors.append(f"Layer '{layer_name}' must have provider and model")
        else:
            prov = str(cfg.get("provider", "")).strip().lower()
            if prov and prov not in ALLOWED_PROVIDERS:
                errors.append(f"Layer '{layer_name}': provider '{prov}' not in {sorted(ALLOWED_PROVIDERS)}")
        fallbacks = cfg.get("fallback_models")
        if fallbacks is not None:
            if not isinstance(fallbacks, list):
                errors.append(f"Layer '{layer_name}': fallback_models must be a list")
            else:
                for i, fb in enumerate(fallbacks):
                    if not isinstance(fb, dict) or "provider" not in fb or "model" not in fb:
                        errors.append(f"Layer '{layer_name}': fallback_models[{i}] must have provider and model")
                    else:
                        p = str(fb.get("provider", "")).strip().lower()
                        if p and p not in ALLOWED_PROVIDERS:
                            errors.append(f"Layer '{layer_name}': fallback[{i}] provider '{p}' not allowed")

    # WORKER_EXECUTION: if any fallback is paid (openai/anthropic/google), must have cfo_gated_paid_fallback
    we = layers.get("WORKER_EXECUTION") if isinstance(layers, dict) else None
    if isinstance(we, dict):
        fallbacks = we.get("fallback_models") or []
        has_paid_fallback = any(
            str(f.get("provider", "")).strip().lower() in ("openai", "anthropic", "google")
            for f in fallbacks if isinstance(f, dict)
        )
        if has_paid_fallback and not we.get("cfo_gated_paid_fallback"):
            errors.append("WORKER_EXECUTION has paid fallback but cfo_gated_paid_fallback is not true")

    return len(errors) == 0, errors


def _to_litellm(provider: str, model: str) -> str:
    """Format provider and model as litellm model string (provider/model)."""
    return f"{provider.strip().lower()}/{model.strip()}"


def resolve_model(
    layer_name: str,
    attempt_index: int = 0,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve model for a layer. attempt_index 0 = primary; 1+ = fallback_models[attempt_index-1].
    Returns dict: provider, model, litellm_model (provider/model), layer, attempt_index, reason.
    """
    data = load_routing()
    layers = data.get("layers") or {}
    layer_name = str(layer_name or "").strip().upper()
    # Normalize layer alias
    if layer_name.startswith("WORKER_"):
        layer_name = "WORKER_EXECUTION"
    if layer_name.startswith("MANAGER_"):
        layer_name = "MANAGER"

    cfg = layers.get(layer_name)
    if not isinstance(cfg, dict):
        # Fallback to CEO for unknown layers so we don't break
        cfg = layers.get("CEO") or {}
    provider = str(cfg.get("provider", "anthropic")).strip().lower()
    model = str(cfg.get("model", "claude-sonnet")).strip()
    fallbacks = cfg.get("fallback_models") or []

    if attempt_index > 0 and 0 <= attempt_index - 1 < len(fallbacks):
        fb = fallbacks[attempt_index - 1]
        if isinstance(fb, dict):
            provider = str(fb.get("provider", provider)).strip().lower()
            model = str(fb.get("model", model)).strip()

    litellm_model = _to_litellm(provider, model)
    return {
        "provider": provider,
        "model": model,
        "litellm_model": litellm_model,
        "layer": layer_name,
        "attempt_index": attempt_index,
        "reason": reason,
    }


def get_fallback_chain(layer_name: str) -> List[Dict[str, Any]]:
    """Return list of {provider, model, litellm_model} for primary + fallbacks (for retry order)."""
    data = load_routing()
    layers = data.get("layers") or {}
    layer_name = str(layer_name or "").strip().upper()
    if layer_name.startswith("WORKER_"):
        layer_name = "WORKER_EXECUTION"
    if layer_name.startswith("MANAGER_"):
        layer_name = "MANAGER"
    cfg = layers.get(layer_name)
    if not isinstance(cfg, dict):
        cfg = layers.get("CEO") or {}
    chain = []
    chain.append({
        "provider": str(cfg.get("provider", "anthropic")).strip().lower(),
        "model": str(cfg.get("model", "claude-sonnet")).strip(),
    })
    for fb in (cfg.get("fallback_models") or []):
        if isinstance(fb, dict):
            chain.append({
                "provider": str(fb.get("provider", "")).strip().lower(),
                "model": str(fb.get("model", "")).strip(),
            })
    for c in chain:
        c["litellm_model"] = _to_litellm(c["provider"], c["model"])
    return chain


def is_worker_paid_fallback_gated() -> bool:
    """True if WORKER_EXECUTION has paid fallback that requires CFO approval."""
    data = load_routing()
    we = (data.get("layers") or {}).get("WORKER_EXECUTION")
    if not isinstance(we, dict):
        return False
    return bool(we.get("cfo_gated_paid_fallback"))


def get_routing_summary(include_secrets: bool = False) -> Dict[str, Any]:
    """Return a summary of routing for /status or self-test (no secrets)."""
    try:
        data = load_routing()
    except Exception as e:
        return {"error": str(e), "layers": {}}
    layers = data.get("layers") or {}
    out: Dict[str, Any] = {"path": _ROUTING_PATH, "layers": {}}
    for name, cfg in layers.items():
        if not isinstance(cfg, dict):
            continue
        out["layers"][name] = {
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "fallback_count": len(cfg.get("fallback_models") or []),
            "cfo_gated_paid_fallback": cfg.get("cfo_gated_paid_fallback"),
        }
    return out


def clear_cached_routing() -> None:
    """Clear cached config (for tests)."""
    global _ROUTING, _ROUTING_PATH
    _ROUTING = None
    _ROUTING_PATH = None
