"""
Thin HTTP client for the Kommodo public v2 API.

Reads KOMMODO_API_TOKEN and optional KOMMODO_API_BASE_URL from env at call time.
The pre-rebrand KOMODO_* names are still accepted as a fallback.
Raises KommodoAPIError on non-2xx responses with status + parsed body.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal


DEFAULT_BASE = "https://kommodo.ai"


class KommodoAPIError(RuntimeError):
    def __init__(self, status: int, body: Any, message: str) -> None:
        super().__init__(f"Kommodo API {status}: {message}")
        self.status = status
        self.body = body


def _base_url() -> str:
    base = os.environ.get("KOMMODO_API_BASE_URL") or os.environ.get("KOMODO_API_BASE_URL")
    return (base or DEFAULT_BASE).rstrip("/")


def _token() -> str:
    # KOMODO_API_TOKEN is the pre-rebrand name, still honoured so existing shells
    # keep working. New setups should export KOMMODO_API_TOKEN.
    tok = os.environ.get("KOMMODO_API_TOKEN") or os.environ.get("KOMODO_API_TOKEN")
    if not tok:
        raise RuntimeError(
            "KOMMODO_API_TOKEN not set. Generate a token at /account?tab=api."
        )
    return tok


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    accept: str = "application/json",
) -> Any:
    url = f"{_base_url()}{path}"
    if params:
        cleaned = {k: v for k, v in params.items() if v is not None}
        if cleaned:
            url += "?" + urllib.parse.urlencode(cleaned)

    data: bytes | None = None
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": accept,
    }
    if body is not None:
        data = _json_dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)

    # One retry on 429 honouring Retry-After; one retry on transient 502.
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return _json_loads(raw.decode("utf-8"))
                return raw.decode("utf-8")
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            try:
                parsed_body = _json_loads(body_text)
            except Exception:
                parsed_body = body_text
            if e.code == 429 and attempt == 0:
                retry_after = int(e.headers.get("Retry-After", "5"))
                time.sleep(min(retry_after, 60))
                continue
            if e.code == 502 and attempt == 0:
                time.sleep(2)
                continue
            message = (
                parsed_body.get("error")
                if isinstance(parsed_body, dict)
                else str(parsed_body)
            )
            raise KommodoAPIError(e.code, parsed_body, message or "request failed")
    raise KommodoAPIError(0, None, "exhausted retries")


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"))


def _json_loads(s: str) -> Any:
    import json

    return json.loads(s)


# ========= Read tools =========


def find_recordings(
    query: str | None = None,
    since: str | None = None,
    until: str | None = None,
    folder_id: str | None = None,
    member_id: str | None = None,
    has_page: bool | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List recordings. Returns {recordings: [...], next_cursor: str | None}."""
    params: dict[str, Any] = {
        "limit": limit,
        "cursor": cursor,
        "q": query,
        "since": since,
        "until": until,
        "folder_id": folder_id,
        "member_id": member_id,
    }
    if has_page is not None:
        params["has_page"] = "true" if has_page else "false"
    return _request("GET", "/api/public/v2/recordings", params=params)


def get_recording(id: str) -> dict[str, Any]:
    """Full RecordingV2 envelope including ai.summary/chapters/action_items."""
    return _request("GET", f"/api/public/v2/recordings/{id}")


def get_transcript(
    id: str, format: Literal["json", "vtt"] = "json"
) -> dict[str, Any] | str:
    """Transcript in JSON cues or raw VTT. Pass format='vtt' for WEBVTT text."""
    path = f"/api/public/v2/recordings/{id}/transcript"
    accept = "text/vtt" if format == "vtt" else "application/json"
    return _request("GET", path, params={"format": format}, accept=accept)


def list_folders(
    parent_id: str | None = None,
    member_id: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """
    {folders: [...], next_cursor: str | None}

    member_id (team-owner + premium tokens only): list folders owned by a
    specific team member. Without it, returns folders the caller owns or has
    been shared on.
    """
    return _request(
        "GET",
        "/api/public/v2/folders",
        params={
            "parent_id": parent_id,
            "member_id": member_id,
            "cursor": cursor,
        },
    )


def list_team_members() -> dict[str, Any]:
    """Team owner and members. Use member ids for member_id filter."""
    return _request("GET", "/api/public/v2/team/members")


# ========= Write tools (require read+write scope token) =========


def update_recording(
    id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    folder_id: str | None | Literal[""] = None,
) -> dict[str, Any]:
    """
    PATCH a recording. Only fields you pass (not None) are updated.

    folder_id semantics:
      None  → don't touch folder
      ""    → remove from folder (clears parentId)
      "…"   → move into folder id
    """
    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if description is not None:
        body["description"] = description
    if tags is not None:
        body["tags"] = tags
    if folder_id is not None:
        body["folder_id"] = folder_id if folder_id != "" else None
    return _request("PATCH", f"/api/public/v2/recordings/{id}", body=body)


def create_page(
    recording_id: str,
    *,
    headline: str | None = None,
    description: str | None = None,
    publish: bool = False,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Convert a recording into a page. 409 if one already exists."""
    body: dict[str, Any] = {"publish": publish}
    if headline is not None:
        body["headline"] = headline
    if description is not None:
        body["description"] = description
    if template_id is not None:
        body["template_id"] = template_id
    return _request(
        "POST",
        f"/api/public/v2/recordings/{recording_id}/pages",
        body=body,
    )


# ========= Convenience helpers =========


def iter_all_recordings(
    **filters: Any,
) -> "list[dict[str, Any]]":
    """Walk cursor pagination; returns a flat list. Use for small result sets only."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        batch = find_recordings(cursor=cursor, **filters)
        out.extend(batch.get("recordings") or [])
        cursor = batch.get("next_cursor")
        if not cursor:
            return out
