import os
from typing import Any, Dict, List
import yaml

def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value

def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        elif k in out and isinstance(out[k], list) and isinstance(v, list) and k in ("checks",):
            out[k] = out[k] + v
        else:
            out[k] = v
    return out

def load_config(path: str) -> Dict[str, Any]:
    base_dir = os.path.dirname(os.path.abspath(path))
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    includes: List[str] = cfg.pop("includes", []) or []
    merged: Dict[str, Any] = {}

    for inc in includes:
        inc_path = inc if os.path.isabs(inc) else os.path.join(base_dir, inc)
        with open(inc_path, "r", encoding="utf-8") as f:
            inc_cfg = yaml.safe_load(f) or {}
        merged = _deep_merge(merged, inc_cfg)

    merged = _deep_merge(merged, cfg)
    return _expand_env(merged)
