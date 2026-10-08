"""
=========================================================
AI SRE AGENT
Module : Path Safety
Purpose:
    Shared validation for IDs that end up embedded in on-disk filenames
    (fix for issue #1 - path traversal via unvalidated
    investigation_id / resource_id).

    Two kinds of IDs flow through this codebase:

    * resource_id - an AWS identifier (EC2 i-..., ALB
      app/my-alb/<hex>, ASG names, target groups) used as the filename
      stem for output/reports/<id>.json, output/context/<id>.json and
      output/prompts/<id>.txt. AWS IDs only ever contain [A-Za-z0-9-]
      plus '/' and '.' for load-balancer target identifiers - nothing
      else, and critically never '..' or a leading '/'.

    * investigation_id - the f"{run_id}__{resource_id}" composite
      (api/follow_up_manager.py / api/log_investigation_manager.py)
      used as the filename stem for output/conversations/<id>.json and
      output/log_investigations/<id>.json.

    Both are validated by the same allowlist regex: instead of trying to
    enumerate what a malicious ID might contain, we enumerate what a
    legitimate ID looks like. Anything outside the allowlist (../
    traversal, absolute paths, backslashes, NULs, etc.) is rejected
    before it ever reaches a Path() join.
=========================================================
"""

import re

# AWS resource identifiers and this project's composite investigation_id
# (run_id__resource_id) both fit this exact profile. Notably it permits
# single dots (ALB target IDs like app/name/80abcdef.2a) but the ".."
# check below blocks the traversal sequence that would make dots
# dangerous, and the lack of a leading "/" or backslash blocks absolute
# paths outright.
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")

# Allows callers to pass very long AWS ARNs/ALB IDs without accepting
# unbounded attacker-controlled strings.
MAX_ID_LENGTH = 512


def validate_file_id(value: str, field: str = "id") -> str:
    """Return `value` if it is safe to embed in a filename, else raise
    ValueError. Pure validation - never throws away or replaces
    characters, so a good ID round-trips unchanged and a bad ID fails
    loudly instead of silently becoming a different file."""
    if not value:
        raise ValueError(f"{field} must not be empty")
    if len(value) > MAX_ID_LENGTH:
        raise ValueError(f"{field} exceeds {MAX_ID_LENGTH} characters")
    if not SAFE_ID_PATTERN.match(value):
        raise ValueError(f"{field} contains disallowed characters: {value!r}")
    if ".." in value:
        raise ValueError(f"{field} must not contain '..': {value!r}")
    return value
