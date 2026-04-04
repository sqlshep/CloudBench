"""Configuration loader with environment variable interpolation and profile merging."""

from __future__ import annotations

import os
import copy
import re
from pathlib import Path
from typing import Any

import yaml


_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")

PRESET_PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "label": "Quick Smoke Test     (~5-15 min)",
        "tests": [
            "random_read", "random_write",
            "integrity", "dsb",
        ],
        "sqlio": {"table_rows": 2_000, "ops_per_run": 100, "thread_counts": [1, 4]},
        "sqliosim": {"write_cycles": 50, "threads": 4, "account_count": 100},
        "dsb": {"scale_factor": 0.01, "selected_queries": ["Q01", "Q06"]},
        "network": {"ping_count": 10, "connection_count": 5, "bandwidth_rows": 1_000},
    },
    "standard": {
        "label": "Standard Benchmark   (~10-20 min)",
        "tests": [
            "random_read", "random_write", "seq_scan", "mixed", "bulk_insert",
            "integrity", "concurrency", "dsb", "net_latency",
        ],
        "sqlio": {"table_rows": 200_000, "ops_per_run": 50_000, "thread_counts": [1, 2, 4, 8, 16]},
        "sqliosim": {"write_cycles": 1_000, "threads": 12, "account_count": 2_000},
        "dsb": {"scale_factor": 0.05, "selected_queries": "all"},
        "network": {"ping_count": 50, "connection_count": 20, "bandwidth_rows": 3_000},
    },
    "full": {
        "label": "Full Stress Test     (~30-90 min)",
        "tests": [
            "random_read", "random_write", "seq_scan", "mixed", "bulk_insert",
            "integrity", "concurrency", "isolation",
            "dsb", "pool_stress", "net_latency",
        ],
        "sqlio": {"table_rows": 500_000, "ops_per_run": 100_000, "thread_counts": [1, 2, 4, 8, 16, 32]},
        "sqliosim": {"write_cycles": 2_000, "threads": 16, "account_count": 5_000},
        "dsb": {"scale_factor": 0.1, "selected_queries": "all", "iterations": 2},
        "network": {"ping_count": 100, "connection_count": 30, "bandwidth_rows": 5_000},
    },
}


def _interpolate_env(value: Any) -> Any:
    """Replace ${VAR} or ${VAR:default} in string values with environment variables."""
    if not isinstance(value, str):
        return value
    def _replace(m: re.Match) -> str:
        var_name = m.group(1)
        default = m.group(2)
        env_val = os.environ.get(var_name)
        if env_val is not None:
            return env_val
        if default is not None:
            return default
        return m.group(0)
    return _ENV_PATTERN.sub(_replace, value)


def _walk_interpolate(obj: Any) -> Any:
    """Recursively interpolate env vars in a config dict."""
    if isinstance(obj, dict):
        return {k: _walk_interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_interpolate(v) for v in obj]
    return _interpolate_env(obj)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def load_config(config_path: str | Path, profile_path: str | Path | None = None) -> dict:
    """Load a YAML config, optionally merge a cloud profile, and interpolate env vars."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}

    if profile_path:
        profile_path = Path(profile_path)
        if not profile_path.exists():
            raise FileNotFoundError(f"Profile not found: {profile_path}")
        with open(profile_path) as f:
            profile = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, profile)

    return _walk_interpolate(cfg)


def apply_preset(cfg: dict, preset_name: str) -> dict:
    """Overlay a preset profile (smoke/standard/full) onto a loaded config."""
    if preset_name not in PRESET_PROFILES:
        raise ValueError(f"Unknown preset: {preset_name}. Choose from: {list(PRESET_PROFILES.keys())}")
    preset = PRESET_PROFILES[preset_name]
    merged = copy.deepcopy(cfg)
    for section in ("sqlio", "sqliosim", "dsb", "network"):
        if section in preset:
            merged[section] = _deep_merge(merged.get(section, {}), preset[section])
    merged["_preset"] = preset_name
    merged["_tests"] = preset["tests"]
    return merged
