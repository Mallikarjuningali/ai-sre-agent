"""
ResourceService — discovered AWS resource inventory, grouped by type.

Backs the Single Resource Investigation launcher on the Investigation page:
the "Resource Type" dropdown lists the keys of this object, the "Resource"
dropdown lists the array for the selected type. Contract (resources.json):

    {
      "EC2 Instance": [ { "id": "i-0123456789abcdef", "label": "Production-Web-01" } ],
      "Load Balancer": [ { "id": "app/prod-alb/50dc6c4950c9188", "label": "prod-alb" } ]
    }

`label` is optional per-entry (defensively falls back to `id`). Read
exclusively through the same DataSource every other service uses, so this
has no opinion about local/S3/REST — see services/CONTRACT.md.
"""
from __future__ import annotations

from .cache import cached
from .data_source import DataSource

RESOURCES_KEY = "resources.json"


class ResourceService:
    def __init__(self, data_source: DataSource, refresh_interval_seconds: int = 30):
        self._ds = data_source
        self._ttl = refresh_interval_seconds

    def get_inventory(self) -> dict[str, list[dict]]:
        def _load():
            if not self._ds.exists(RESOURCES_KEY):
                return {}
            raw = self._ds.read_json(RESOURCES_KEY)
            return raw if isinstance(raw, dict) else {}

        return cached(f"resources::{RESOURCES_KEY}", self._ttl, _load)

    def get_resource_types(self) -> list[str]:
        return list(self.get_inventory().keys())

    def get_resources_for_type(self, resource_type: str) -> list[dict]:
        entries = self.get_inventory().get(resource_type) or []
        return [e for e in entries if isinstance(e, dict) and e.get("id")]
