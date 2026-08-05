"""
DataSource abstraction.

The dashboard never talks to AWS directly. It reads JSON objects that the
AegisOps AI backend (running on its own EC2 instance) has already produced.
Today that means JSON files dropped in an S3 bucket; tomorrow it may mean a
REST API. Every service in this package depends only on the `DataSource`
interface below, so switching backends is a one-line config change (see
services/config.py and the Settings page) and never touches UI code.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .config import AppConfig, DASHBOARD_ROOT


class DataSourceError(Exception):
    """Raised when the configured backend cannot serve a requested key."""


class DataSource(ABC):
    """Read-only access to backend-produced JSON objects, keyed by path."""

    @abstractmethod
    def read_json(self, key: str) -> Any:
        """Return the parsed JSON object stored at `key`."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if `key` currently resolves to an object."""

    @property
    @abstractmethod
    def label(self) -> str:
        """Human-readable description shown in the Settings page."""


class LocalFileDataSource(DataSource):
    """Reads JSON fixtures from disk. Used for local development/offline demo."""

    def __init__(self, base_dir: str):
        self._base_dir = (DASHBOARD_ROOT / base_dir).resolve()

    def _path_for(self, key: str) -> Path:
        return self._base_dir / key

    def read_json(self, key: str) -> Any:
        path = self._path_for(key)
        if not path.exists():
            raise DataSourceError(f"Local data file not found: {path}")
        try:
            with path.open("r") as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            raise DataSourceError(f"Invalid JSON in {path}: {exc}") from exc

    def exists(self, key: str) -> bool:
        return self._path_for(key).exists()

    @property
    def label(self) -> str:
        return f"Local files ({self._base_dir})"


class S3DataSource(DataSource):
    """Reads JSON objects written by the backend to an S3 bucket."""

    def __init__(self, bucket: str, prefix: str = "", region: str | None = None):
        if not bucket:
            raise DataSourceError("S3 data source selected but no bucket configured.")
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/" if prefix else ""
        self._region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    def _object_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def read_json(self, key: str) -> Any:
        client = self._get_client()
        object_key = self._object_key(key)
        try:
            response = client.get_object(Bucket=self._bucket, Key=object_key)
            body = response["Body"].read()
            return json.loads(body)
        except Exception as exc:  # noqa: BLE001 - surface as DataSourceError to callers
            raise DataSourceError(f"Failed to read s3://{self._bucket}/{object_key}: {exc}") from exc

    def exists(self, key: str) -> bool:
        client = self._get_client()
        try:
            client.head_object(Bucket=self._bucket, Key=self._object_key(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    @property
    def label(self) -> str:
        return f"S3 (s3://{self._bucket}/{self._prefix})"


class RestApiDataSource(DataSource):
    """Reads JSON objects from a REST API exposed by the backend (future)."""

    def __init__(self, base_url: str, api_key: str | None = None):
        if not base_url:
            raise DataSourceError("REST data source selected but no base URL configured.")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def read_json(self, key: str) -> Any:
        import requests

        url = f"{self._base_url}/{key.lstrip('/')}"
        try:
            response = requests.get(url, headers=self._headers(), timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise DataSourceError(f"Failed to GET {url}: {exc}") from exc

    def exists(self, key: str) -> bool:
        import requests

        url = f"{self._base_url}/{key.lstrip('/')}"
        try:
            response = requests.head(url, headers=self._headers(), timeout=10)
            return response.ok
        except Exception:  # noqa: BLE001
            return False

    @property
    def label(self) -> str:
        return f"REST API ({self._base_url})"


def get_data_source(config: AppConfig) -> DataSource:
    """Factory: build the configured DataSource. This is the single switch point."""
    if config.data_source_type == "s3":
        return S3DataSource(bucket=config.s3_bucket, prefix=config.s3_prefix, region=config.aws_region)
    if config.data_source_type == "rest":
        return RestApiDataSource(base_url=config.rest_base_url, api_key=config.rest_api_key)
    return LocalFileDataSource(base_dir=config.local_data_dir)
