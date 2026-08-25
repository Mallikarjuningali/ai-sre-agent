"""
=========================================================
AI SRE AGENT
Module : API
Purpose:
    Exposes the existing collector -> context builder -> analyzer ->
    dashboard export pipeline over HTTP for the AegisOps AI dashboard's
    Investigation launcher (see dashboard/services/CONTRACT.md,
    "Investigation actions" section, for the request/response contract
    these routes implement).

    Also serves the published dashboard feed (output/dashboard_feed/*.json)
    read-only, so RestApiDataSource (dashboard/services/data_source.py) has
    something to GET <base_url>/<file>.json against in REST mode - see
    CONTRACT.md for what each file contains. These routes only read files
    that utils/dashboard_export.py already wrote; they don't build or
    compute feed data themselves.

    Also exposes DELETE /executions for the History page's "Delete
    Selected" action (see utils/execution_cleanup.py for what actually gets
    removed) - this is the one write path here that isn't about starting an
    investigation.

    Reuses main.py's own Analyzer/collector entry points via
    InvestigationManager - no collection or analysis logic lives here.

    No auth yet - the dashboard doesn't send an Authorization header while
    the REST wiring is still being brought up end-to-end. Add API-key auth
    (require_api_key style dependency, checked against an env var, applied
    via FastAPI's `dependencies=`) once the dashboard is fully working over
    REST and can be updated to send the token in the same change.

Run (from the ai-sre-agent/ project root, same cwd main.py expects):
    uvicorn api.app:app --host 0.0.0.0 --port 8000
=========================================================
"""

import json
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from api.investigation_manager import InvestigationBusyError, InvestigationManager
from api.cost_explorer_manager import CostExplorerBusyError, CostExplorerManager
from utils.dashboard_export import FEED_DIR, build_resources_json, load_current_contexts
from utils.cost_dashboard_export import FEED_DIR as COST_FEED_DIR
from utils.execution_cleanup import delete_executions

app = FastAPI(title="AI SRE Agent API")

manager = InvestigationManager()

# Cost Explorer is a completely separate pipeline (own collector, own
# Gemini analysis, own feed directory) - its own manager/lock so a cost
# refresh can never collide with an infra investigation.
cost_manager = CostExplorerManager()


class ResourceInvestigationRequest(BaseModel):
    resource_type: str
    resource_id: str


class DeleteExecutionsRequest(BaseModel):
    run_ids: list[str]


@app.post("/investigation/full")
def start_full_investigation():
    try:
        return manager.start_full()
    except InvestigationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/investigation/resource")
def start_resource_investigation(payload: ResourceInvestigationRequest):
    try:
        return manager.start_resource(payload.resource_type, payload.resource_id)
    except InvestigationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/investigation/status/{run_id}")
def get_investigation_status(run_id: str):
    status = manager.get_status(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    return status


@app.get("/resources")
def get_resources():
    return build_resources_json(load_current_contexts())


@app.delete("/executions")
def delete_executions_endpoint(payload: DeleteExecutionsRequest):
    if not payload.run_ids:
        raise HTTPException(status_code=400, detail="run_ids must not be empty")

    running = sorted(
        run_id for run_id in payload.run_ids
        if (status := manager.get_status(run_id)) and status.get("status") in ("QUEUED", "RUNNING")
    )
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete running investigation(s): {', '.join(running)}",
        )

    result = delete_executions(payload.run_ids)
    if not result["deleted"]:
        raise HTTPException(status_code=404, detail="No matching executions found to delete")
    return result


# -----------------------------------------------------------------------
# Dashboard feed - read-only passthrough of output/dashboard_feed/*.json
# for RestApiDataSource. Every file here is written by
# utils.dashboard_export.export(); these routes never generate feed data.
# -----------------------------------------------------------------------

def _read_feed_file(filename: str):
    path = FEED_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# RestApiDataSource.exists() (dashboard/services/data_source.py) HEADs these
# same paths before GETting them. FastAPI/Starlette's automatic "GET implies
# HEAD" support runs the GET handler and does not strip its body, so a plain
# @app.get route would send the full JSON payload on a HEAD request instead
# of an empty one. These explicit HEAD routes are registered before the
# matching GET route below so the router matches them first for HEAD
# requests - see Starlette's Router.app(), which dispatches to the first
# route whose (path, method) both match.

def _feed_exists_status(filename: str) -> int:
    return 200 if (FEED_DIR / filename).exists() else 404


@app.head("/summary.json")
def head_summary_feed():
    return Response(status_code=_feed_exists_status("summary.json"), media_type="application/json")


@app.head("/executions.json")
def head_executions_feed():
    return Response(status_code=_feed_exists_status("executions.json"), media_type="application/json")


@app.head("/collectors.json")
def head_collectors_feed():
    return Response(status_code=_feed_exists_status("collectors.json"), media_type="application/json")


@app.head("/reports.json")
def head_reports_feed():
    return Response(status_code=_feed_exists_status("reports.json"), media_type="application/json")


@app.head("/analytics.json")
def head_analytics_feed():
    return Response(status_code=_feed_exists_status("analytics.json"), media_type="application/json")


@app.head("/resources.json")
def head_resources_feed():
    return Response(status_code=_feed_exists_status("resources.json"), media_type="application/json")


@app.get("/summary.json")
def get_summary_feed():
    return _read_feed_file("summary.json")


@app.get("/executions.json")
def get_executions_feed():
    return _read_feed_file("executions.json")


@app.get("/collectors.json")
def get_collectors_feed():
    return _read_feed_file("collectors.json")


@app.get("/reports.json")
def get_reports_feed():
    return _read_feed_file("reports.json")


@app.get("/analytics.json")
def get_analytics_feed():
    return _read_feed_file("analytics.json")


@app.get("/resources.json")
def get_resources_feed():
    return _read_feed_file("resources.json")


# -----------------------------------------------------------------------
# Cost Explorer - completely separate pipeline/feed from everything above.
# POST /cost-explorer/refresh triggers a fresh boto3 Cost Explorer query
# (collector -> CostAnalyzer -> cost_dashboard_export), synchronously -
# these calls return in low single digit seconds, so no run_id/polling
# machinery is used here, unlike /investigation/full. The GET/HEAD routes
# below are pure passthroughs of output/cost/dashboard_feed/*.json,
# exactly like the infra feed routes above are for output/dashboard_feed/.
# -----------------------------------------------------------------------

class CostRefreshRequest(BaseModel):
    # Both optional, and only meaningful together: the user-selected
    # Month/Period Comparison range from the dashboard's date pickers.
    # Omitted entirely (or an empty {} body, which is what every existing
    # caller already sends) -> the refresh behaves exactly as it did
    # before this feature existed, so no existing caller breaks.
    from_date: Optional[str] = None
    to_date: Optional[str] = None


@app.post("/cost-explorer/refresh")
def refresh_cost_explorer(payload: Optional[CostRefreshRequest] = None):
    from_date = payload.from_date if payload else None
    to_date = payload.to_date if payload else None
    try:
        return cost_manager.refresh(from_date=from_date, to_date=to_date)
    except CostExplorerBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cost Explorer refresh failed: {exc}")


def _read_cost_feed_file(filename: str):
    path = COST_FEED_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _cost_feed_exists_status(filename: str) -> int:
    return 200 if (COST_FEED_DIR / filename).exists() else 404


@app.head("/cost-explorer/summary")
def head_cost_summary_feed():
    return Response(status_code=_cost_feed_exists_status("summary.json"), media_type="application/json")


@app.head("/cost-explorer/history")
def head_cost_history_feed():
    return Response(status_code=_cost_feed_exists_status("history.json"), media_type="application/json")


@app.head("/cost-explorer/credits")
def head_cost_credits_feed():
    return Response(status_code=_cost_feed_exists_status("credits.json"), media_type="application/json")


@app.head("/cost-explorer/services")
def head_cost_services_feed():
    return Response(status_code=_cost_feed_exists_status("services.json"), media_type="application/json")


@app.head("/cost-explorer/regions")
def head_cost_regions_feed():
    return Response(status_code=_cost_feed_exists_status("regions.json"), media_type="application/json")


@app.head("/cost-explorer/anomalies")
def head_cost_anomalies_feed():
    return Response(status_code=_cost_feed_exists_status("anomalies.json"), media_type="application/json")


@app.head("/cost-explorer/comparison")
def head_cost_comparison_feed():
    return Response(status_code=_cost_feed_exists_status("comparison.json"), media_type="application/json")


@app.head("/cost-explorer/report")
def head_cost_report_feed():
    return Response(status_code=_cost_feed_exists_status("report.json"), media_type="application/json")


@app.get("/cost-explorer/summary")
def get_cost_summary_feed():
    return _read_cost_feed_file("summary.json")


@app.get("/cost-explorer/history")
def get_cost_history_feed():
    return _read_cost_feed_file("history.json")


@app.get("/cost-explorer/credits")
def get_cost_credits_feed():
    return _read_cost_feed_file("credits.json")


@app.get("/cost-explorer/services")
def get_cost_services_feed():
    return _read_cost_feed_file("services.json")


@app.get("/cost-explorer/regions")
def get_cost_regions_feed():
    return _read_cost_feed_file("regions.json")


@app.get("/cost-explorer/anomalies")
def get_cost_anomalies_feed():
    return _read_cost_feed_file("anomalies.json")


@app.get("/cost-explorer/comparison")
def get_cost_comparison_feed():
    # May legitimately be `null` (valid JSON) when no comparison has
    # been requested yet - _read_cost_feed_file() only 404s when the
    # file itself is missing, not when its content is null.
    return _read_cost_feed_file("comparison.json")


@app.get("/cost-explorer/report")
def get_cost_report_feed():
    return _read_cost_feed_file("report.json")
