# AegisOps AI — Dashboard Data Contract

The dashboard never talks to AWS. It reads JSON objects published by the
backend to the configured `DataSource` (local files today, S3 initially in
production, REST API later — see `data_source.py` / Settings page). This
document is the contract each service expects. Fields marked **core** are
exactly what was specified for the backend; fields marked **optional** are
extensions the UI reads defensively (safe defaults when missing) to support
extra table columns / linking, they are not required for the app to run.

Every key below is resolved relative to the data source root
(e.g. local: `assets/sample_data/<key>`, S3: `s3://<bucket>/<prefix><key>`).

## `summary.json` — SummaryService (Overview page)

```jsonc
{
  "generated_at": "2026-08-04T12:00:10Z",
  "kpis": {
    "total_resources":  { "value": 156, "delta": 12,   "delta_label": "from yesterday" },
    "critical_issues":  { "value": 8,   "delta": 3,    "delta_label": "from yesterday" },
    "high_severity":    { "value": 15,  "delta": -2,   "delta_label": "from yesterday" },
    "investigations":   { "value": 24,  "delta": 5,    "delta_label": "from yesterday" },
    "success_rate":     { "value": 95.8, "delta": 2.3, "delta_label": "from yesterday" }
  },
  "infrastructure_health": { "total": 156, "healthy": 98, "warning": 32, "critical": 18, "unknown": 8 },
  "latest_execution": { "run_id": "...", "status": "COMPLETED", "resources": 156, "successful": 150, "failed": 6, "duration_seconds": 272, "start_time": "..." },
  "incidents_by_severity_trend": [ { "date": "2026-04-28", "critical": 8, "high": 10, "medium": 6, "low": 4 } ],
  "top_affected_resources": [ { "resource_id": "i-...", "type": "EC2 Instance", "incidents": 6 } ],
  "investigation_trend": [ { "date": "2026-04-28", "count": 12 } ],
  "recent_investigations": [ { "report_id": "i-...", "severity": "CRITICAL", "summary": "...", "detected_at": "2026-08-04T11:50:00Z" } ]
}
```

## `collectors.json` — CollectorService (Overview + Analytics)

**Core**, exact shape:

```json
{ "CloudWatch": "Healthy", "CloudTrail": "Healthy", "ALB": "Healthy", "AutoScaling": "Healthy" }
```

Any string value is accepted; the UI treats case-insensitive `"healthy"` as
OK and anything else as needing attention.

## `executions.json` — HistoryService (History page, Overview "Latest Executions")

Array of Execution objects. **Core** fields:

```json
{ "run_id": "20260804_102030", "execution_time": 23, "successful": 5, "failed": 0, "resources": 5 }
```

**Optional** additions used by the History/Overview tables: `type`
(e.g. `"Full Investigation"`), `status` (e.g. `"COMPLETED"`, `"RUNNING"`,
`"FAILED"`), `start_time` (ISO-8601), `finished_at` (ISO-8601),
`resources_analyzed` (attempted, whether or not it succeeded -
distinct from `successful`), `resources_skipped` (e.g. terminated/
shutting-down EC2 instances a Full Investigation didn't bother analyzing),
`reports_generated` (successes only). Used by the Report Viewer's
execution overview card - see `report_viewer.py`.

## `reports.json` — ReportService (Investigation page)

Array of Report objects. **Core** fields:

```json
{
  "instance_id": "i-123456",
  "severity": "HIGH",
  "summary": "ALB timeout",
  "root_cause": "Nginx service stopped",
  "evidence": ["..."],
  "recommendations": ["..."]
}
```

**Optional** additions: `report_id`, `run_id` (links the report back to the
execution that produced it), `resource_type`, `detected_at`,
`ai_confidence` (0-1), `telemetry` (the resource's own CloudWatch snapshot
at analysis time - backs the Report Viewer's Telemetry card):

```json
"telemetry": {
  "state": "running", "cpu": 92.4, "memory": 88.1, "disk": 74.0,
  "network_in": 1203981, "network_out": 998211, "status_check": "PASS"
}
```

and `resource_tree` — a nested topology used by the
Investigation page's Resource Tree panel:

```json
"resource_tree": {
  "id": "app/my-alb/50dc6c4950c9188", "type": "Load Balancer", "status": "WARNING",
  "children": [
    { "id": "i-0a1b2c3d4e5f67890", "type": "EC2 Instance", "status": "CRITICAL", "children": [] }
  ]
}
```

## `resources.json` — ResourceService (Investigation page launcher)

Discovered AWS resource inventory, grouped by type. Backs the "Resource
Type" / "Resource" dropdowns in the Single Resource Investigation launcher:

```jsonc
{
  "EC2 Instance": [
    { "id": "i-0123456789abcdef", "label": "Production-Web-01" }
  ],
  "Load Balancer": [
    { "id": "app/prod-alb/50dc6c4950c9188", "label": "prod-alb" }
  ]
}
```

Keys are free-text resource type labels (shown verbatim in the Resource
Type dropdown); each entry needs `id` (passed back as `resource_id` when
starting an investigation), `label` is **optional** and falls back to `id`
when absent.

## `analytics.json` — AnalyticsService (Analytics page)

```jsonc
{
  "incident_trend": [ { "date": "2026-04-28", "count": 12 } ],
  "severity_trend": [ { "date": "2026-04-28", "critical": 8, "high": 10, "medium": 6, "low": 4 } ],
  "resource_distribution": [ { "type": "EC2 Instance", "count": 64 } ],
  "collector_statistics": [ { "name": "CloudWatch", "events_processed": 128400, "errors": 2, "avg_latency_ms": 118 } ],
  "execution_time_trend": [ { "date": "2026-04-28", "avg_seconds": 214 } ]
}
```

## Investigation actions — InvestigationService (Investigation page launcher)

The only *write* paths in the dashboard. Unlike everything above, these are
plain HTTP calls made directly by `services/investigation_service.py`, not
reads through `DataSource` — only available when the configured data source
is `rest` (see Settings page). Local/S3 mode surfaces a clear "connect a
REST backend" message in the UI instead of faking a run.

```
POST /investigation/full
  request:  {}
  response: { "run_id": "20260806_143000", "status": "QUEUED", "started_at": "2026-08-06T14:30:00Z" }

POST /investigation/resource
  request:  { "resource_type": "EC2 Instance", "resource_id": "i-0123456789abcdef" }
  response: { "run_id": "20260806_143000", "status": "QUEUED", "started_at": "2026-08-06T14:30:00Z" }

GET /investigation/status/{run_id}
  response: {
    "run_id": "20260806_143000",
    "status": "RUNNING",                        // QUEUED | RUNNING | COMPLETED | FAILED
    "phase": "RUNNING_AI_ANALYSIS",
    "phase_label": "Running AI Analysis",
    "percent": 62,
    "resource_type": "EC2 Instance",            // null for a Full Infrastructure run
    "resource_id": "i-0a1b2c3d4e5f67890",        // null for a Full Infrastructure run
    "current_resource": "i-0a1b2c3d4e5f67890",   // changes as a Full run works through resources
    "elapsed_seconds": 145,
    "remaining_seconds_estimate": 90,
    "phases": [
      { "key": "COLLECTING_METRICS",   "label": "Collecting Metrics",   "state": "done" },
      { "key": "BUILDING_CONTEXT",     "label": "Building Context",     "state": "done" },
      { "key": "RUNNING_AI_ANALYSIS",  "label": "Running AI Analysis",  "state": "active" },
      { "key": "GENERATING_RCA",       "label": "Generating RCA",       "state": "pending" },
      { "key": "PUBLISHING_DASHBOARD", "label": "Publishing Dashboard", "state": "pending" }
    ]
  }
```

`GET /investigation/status/{run_id}` should return `404` (client treats as
`None`) until the run exists. On `COMPLETED`, the dashboard expects the run
to also show up in `executions.json` / `reports.json` per the contracts
above so the operator can jump straight to the report.

## Execution management — ExecutionService (History page)

The other write path, made by `services/execution_service.py` the same way
as Investigation actions above (REST-only; local/S3 surfaces a clear error
instead of pretending to delete fixture data).

```
DELETE /executions
  request:  { "run_ids": ["06-08-2026_15-23-12", "06-08-2026_18-40-02"] }
  response: { "deleted": ["06-08-2026_15-23-12"], "not_found": ["06-08-2026_18-40-02"] }
```

Deletes each run's summary, archived snapshot, and any current report that
still belongs to it (see `utils/execution_cleanup.py`), then republishes
the dashboard feed once so `executions.json` / `reports.json` /
`summary.json` / `analytics.json` / `collectors.json` reflect the change
immediately - the dashboard doesn't need to wait for the next investigation
to see it disappear.

Returns `400` for an empty `run_ids` list, `409` if any requested `run_id`
is currently `QUEUED`/`RUNNING` (a running investigation is never deleted),
and `404` if none of the requested `run_ids` matched anything. A partial
match (some deleted, some not found) is a `200` with both arrays populated.

## Adding a new backend

Implement `DataSource` in `services/data_source.py` (see
`RestApiDataSource` for the shape) and add a branch to `get_data_source()`.
No service or component code changes.
