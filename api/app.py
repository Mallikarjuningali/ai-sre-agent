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

    Reuses main.py's own Analyzer/collector entry points via
    InvestigationManager - no collection or analysis logic lives here.

Run (from the ai-sre-agent/ project root, same cwd main.py expects):
    uvicorn api.app:app --host 0.0.0.0 --port 8000
=========================================================
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from api.investigation_manager import InvestigationBusyError, InvestigationManager
from utils.dashboard_export import build_resources_json, load_current_contexts

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
