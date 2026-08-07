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

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from api.investigation_manager import InvestigationBusyError, InvestigationManager
from utils.dashboard_export import FEED_DIR, build_resources_json, load_current_contexts

app = FastAPI(title="AI SRE Agent API")

manager = InvestigationManager()


class ResourceInvestigationRequest(BaseModel):
    resource_type: str
    resource_id: str


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
