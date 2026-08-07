"""
=========================================================
AI SRE AGENT
Module : Execution Cleanup
Purpose:
    Deletes a completed run's data for the Execution History page's
    "Delete Selected" action (see api/app.py's DELETE /executions and
    dashboard/services/CONTRACT.md's "Execution management" section).

    Only ever touches:
      - output/summary/<run_id>.json      (makes the run disappear from
                                             executions.json)
      - output/archive/<run_id>/          (raw/context/reports/logs/summary
                                             snapshot from that run)
      - output/reports/<instance_id>.json (only when its content is byte-
                                             for-byte identical to what's
                                             archived for that run - i.e. no
                                             later run has since overwritten
                                             it with a newer report)

    That last check matters: output/reports/<instance_id>.json holds the
    *current* report per resource, not one file per run, so deleting a run
    must never delete a newer run's report just because it happens to be
    for the same resource. Comparing against the archived copy is how we
    know the current file still belongs to the run being deleted.

    Reuses dashboard_export's own path constants and export() so the
    dashboard feed is rebuilt the same way a real run publishes it - no
    feed-building logic is duplicated here.
=========================================================
"""

import re
import shutil

from utils.dashboard_export import ARCHIVE_DIR, REPORTS_DIR, SUMMARY_DIR, export as export_dashboard_feed, read_json

# Matches ExecutionSummary's run_id format exactly (utils/execution_summary.py,
# "%d-%m-%Y_%H-%M-%S"). Validated before it's ever used to build a filesystem
# path, since it arrives from an HTTP request body.
_RUN_ID_RE = re.compile(r"^\d{2}-\d{2}-\d{4}_\d{2}-\d{2}-\d{2}$")


def _is_valid_run_id(run_id) -> bool:
    return isinstance(run_id, str) and bool(_RUN_ID_RE.match(run_id))


def _delete_run(run_id: str) -> bool:
    """Delete one run's data. Returns True if there was anything to delete."""
    found = False

    summary_path = SUMMARY_DIR / f"{run_id}.json"
    if summary_path.exists():
        summary_path.unlink()
        found = True

    archive_dir = ARCHIVE_DIR / run_id
    archived_reports_dir = archive_dir / "reports"
    if archived_reports_dir.exists():
        for archived_report in archived_reports_dir.glob("*.json"):
            current_report = REPORTS_DIR / archived_report.name
            if current_report.exists() and read_json(current_report) == read_json(archived_report):
                current_report.unlink()

    if archive_dir.exists():
        shutil.rmtree(archive_dir)
        found = True

    return found


def delete_executions(run_ids: list) -> dict:
    """Delete each run_id's data, then rebuild the dashboard feed once so
    the change is visible on the next read. Returns
    {"deleted": [...], "not_found": [...]}."""
    deleted = []
    not_found = []

    for run_id in run_ids:
        if not _is_valid_run_id(run_id):
            not_found.append(run_id)
            continue
        if _delete_run(run_id):
            deleted.append(run_id)
        else:
            not_found.append(run_id)

    if deleted:
        export_dashboard_feed()

    return {"deleted": deleted, "not_found": not_found}
