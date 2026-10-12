"""
=========================================================
AI SRE AGENT
Module : API Authentication (issue #2)
Purpose:
    API-key authentication for every route on the app. The app binds
    0.0.0.0 and exposes money-spending triggers (POST /investigation/*,
    POST /cost-explorer/refresh) and a destructive DELETE /executions,
    so an unauthenticated listener on a public interface is not an
    acceptable default.

    Model: a single shared key, sent as `Authorization: Bearer <key>`.
    The dashboard's REST backends already emit exactly this header for
    every request (dashboard/services/*.py, from AppConfig.rest_api_key),
    so wiring the two sides together means having both read the SAME
    environment variable:

        AEGISOPS_REST_API_KEY

    Fail-closed semantics (deliberate):
      * env var unset or empty  -> every request is refused with 503.
        Running "open because nobody configured a key" is exactly the
        vulnerability this module exists to remove; an operator who
        hasn't set the key gets a loud, explicit error instead of a
        silently unauthenticated API.
      * missing/wrong header    -> 401 (unauthenticated), never 403 -
        there are no roles here, only "has the key" or not.
      * comparison is constant-time (hmac.compare_digest) so the
        response time of 401s doesn't leak how much of a guessed
        prefix is correct.

    Applied once, app-wide, via FastAPI(dependencies=[...]) in
    api/app.py - no per-route annotation to forget on the next route
    somebody adds.
=========================================================
"""

import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request

API_KEY_ENV_VAR = "AEGISOPS_REST_API_KEY"


def _configured_api_key() -> Optional[str]:
    """The key the server expects, or None if not configured. Read from
    the environment on every call so a process manager that injects the
    var after startup (systemd EnvironmentFile reload, docker restart
    with env) takes effect without a code change - and so tests can
    monkeypatch os.environ without import-time ordering games."""
    key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    return key or None


def require_api_key(request: Request) -> None:
    """FastAPI dependency: raises unless the request carries the
    configured Bearer key. Returns None (FastAPI treats the dependency
    as satisfied) on success."""
    expected = _configured_api_key()
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"API key not configured on the server: set {API_KEY_ENV_VAR}. "
                "Refusing all requests rather than running unauthenticated."
            ),
        )

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Missing API key: send 'Authorization: Bearer <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
