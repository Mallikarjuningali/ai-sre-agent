"""
Runtime configuration for AegisOps AI dashboard.

Controls which DataSource backs the services layer. Resolution order for
every field is: local settings file (assets/config/settings.json, written
by the Settings page) > environment variable > built-in default. This lets
an EC2 deployment configure itself purely via environment variables while
still allowing an operator to override values live from the Settings page.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = DASHBOARD_ROOT / "assets" / "config" / "settings.json"

VALID_DATA_SOURCES = ("local", "s3", "rest")


@dataclass
class AppConfig:
    # "local"  -> read JSON fixtures from disk (dashboard dev/offline mode)
    # "s3"     -> read JSON objects written by the backend to an S3 bucket
    # "rest"   -> call a REST API exposed by the backend (future)
    data_source_type: str = "local"

    # local
    local_data_dir: str = "assets/sample_data"

    # s3
    s3_bucket: str = ""
    s3_prefix: str = "aegisops/"
    aws_region: str = "us-east-1"

    # rest (reserved for when the backend exposes an API)
    rest_base_url: str = ""
    rest_api_key: str = ""

    # UI / behaviour
    theme: str = "dark"
    refresh_interval_seconds: int = 30

    def to_dict(self) -> dict:
        return asdict(self)


_ENV_MAP = {
    "data_source_type": "AEGISOPS_DATA_SOURCE",
    "local_data_dir": "AEGISOPS_LOCAL_DATA_DIR",
    "s3_bucket": "AEGISOPS_S3_BUCKET",
    "s3_prefix": "AEGISOPS_S3_PREFIX",
    "aws_region": "AEGISOPS_AWS_REGION",
    "rest_base_url": "AEGISOPS_REST_BASE_URL",
    "rest_api_key": "AEGISOPS_REST_API_KEY",
    "theme": "AEGISOPS_THEME",
    "refresh_interval_seconds": "AEGISOPS_REFRESH_INTERVAL",
}

_INT_FIELDS = {"refresh_interval_seconds"}


def _env_defaults() -> dict:
    values = {}
    for field_name, env_name in _ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        values[field_name] = int(raw) if field_name in _INT_FIELDS else raw
    return values


def load_config() -> AppConfig:
    """Load config: defaults -> env vars -> persisted settings.json."""
    values = _env_defaults()

    if SETTINGS_PATH.exists():
        try:
            with SETTINGS_PATH.open("r") as f:
                persisted = json.load(f)
            values.update({k: v for k, v in persisted.items() if k in AppConfig.__dataclass_fields__})
        except (json.JSONDecodeError, OSError):
            pass

    config = AppConfig(**values)
    if config.data_source_type not in VALID_DATA_SOURCES:
        config.data_source_type = "local"
    return config


def save_config(config: AppConfig) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_PATH.open("w") as f:
        json.dump(config.to_dict(), f, indent=2)
