"""
Thin HTTP client for the Kommodo public v2 API.

Reads KOMMODO_API_TOKEN and optional KOMMODO_API_BASE_URL from env at call time.
The pre-rebrand KOMODO_* names are still accepted as a fallback.
Raises KommodoAPIError on non-2xx responses with status + parsed body.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Literal
from xml.sax.saxutils import escape

DEFAULT_BASE = "https://kommodo.ai"


class KommodoAPIError(RuntimeError):
    def __init__(self, status: int, body: Any, message: str) -> None:
        super().__init__(f"Kommodo API {status}: {message}")
        self.status = status
        self.body = body


def _base_url() -> str:
    base = os.environ.get("KOMMODO_API_BASE_URL") or os.environ.get(
        "KOMODO_API_BASE_URL"
    )
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
    retry_server_errors: bool = False,
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

    # Ordinary calls get one retry on 429/502. Upload finalization opts into a
    # third attempt for transient 5xx responses because its token is reusable.
    attempts = 3 if retry_server_errors else 2
    for attempt in range(attempts):
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
            can_retry = attempt < attempts - 1
            if e.code == 429 and can_retry:
                retry_after = int(e.headers.get("Retry-After", "5"))
                time.sleep(min(retry_after, 60))
                continue
            if (
                e.code == 502 or (retry_server_errors and e.code >= 500)
            ) and can_retry:
                time.sleep(attempt + 1)
                continue
            message = (
                parsed_body.get("error")
                if isinstance(parsed_body, dict)
                else str(parsed_body)
            )
            raise KommodoAPIError(e.code, parsed_body, message or "request failed")
        except urllib.error.URLError:
            if retry_server_errors and attempt < attempts - 1:
                time.sleep(attempt + 1)
                continue
            raise
    raise KommodoAPIError(0, None, "exhausted retries")


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"))


def _json_loads(s: str) -> Any:
    import json

    return json.loads(s)


# ========= Read helpers =========


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
    limit: int = 20,
) -> dict[str, Any]:
    """
    {folders: [...], next_cursor: str | None}

    member_id: list folders owned by a member visible under the caller's
    account policy. Without it, returns folders the caller owns or has been
    shared on.
    """
    return _request(
        "GET",
        "/api/public/v2/folders",
        params={
            "parent_id": parent_id,
            "member_id": member_id,
            "cursor": cursor,
            "limit": limit,
        },
    )


def list_team_members() -> dict[str, Any]:
    """Account roster for authorized owners. Use ids for member_id filters."""
    return _request("GET", "/api/public/v2/team/members")


# ========= Write helpers (require read+write scope token) =========


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


def create_folder(name: str, parent_id: str | None = None) -> dict[str, Any]:
    """Create below an owned parent_id, or below the caller's root if omitted."""
    body: dict[str, Any] = {"name": name}
    if parent_id is not None:
        body["parent_id"] = parent_id
    return _request("POST", "/api/public/v2/folders", body=body)


def rename_folder(id: str, name: str) -> dict[str, Any]:
    """Rename a folder the caller owns or has full access to."""
    return _request("PATCH", f"/api/public/v2/folders/{id}", body={"name": name})


def delete_folder(id: str) -> dict[str, Any]:
    """Delete an empty non-root folder; items in trash also block deletion."""
    return _request("DELETE", f"/api/public/v2/folders/{id}")


def create_page(
    recording_id: str,
    *,
    headline: str | None = None,
    description: str | None = None,
    publish: bool = False,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Create a default-layout page. template_id is metadata; 409 if one exists."""
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


def _storage_request(
    url: str,
    *,
    method: str,
    data: bytes | None = None,
    content_type: str | None = None,
) -> Any:
    headers = {"Content-Type": content_type} if content_type else {}
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )
    return urllib.request.urlopen(request, timeout=120)


def upload_video(
    file_path: str | os.PathLike[str],
    *,
    title: str | None = None,
    concurrency: int = 4,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Upload a local video and queue it for processing."""
    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"Video file not found: {path}")
    if concurrency < 1 or concurrency > 16:
        raise ValueError("concurrency must be between 1 and 16")

    size = path.stat().st_size
    if size < 1:
        raise ValueError("Video file is empty")
    initialized = _request(
        "POST",
        "/api/public/v2/uploads",
        body={"type": "video", "title": title or path.name, "size_bytes": size},
    )
    upload = initialized.get("upload") if isinstance(initialized, dict) else None
    providers = upload.get("providers") if isinstance(upload, dict) else None
    chunk_size = upload.get("chunk_size_bytes") if isinstance(upload, dict) else None
    if not providers or not isinstance(chunk_size, int) or chunk_size < 1:
        raise RuntimeError("Kommodo returned an invalid multipart upload contract")

    expected_parts = (size + chunk_size - 1) // chunk_size
    completion = initialized.get("completion")
    if not isinstance(completion, dict):
        raise RuntimeError("Kommodo omitted the upload completion grant")
    completion_path = completion.get("url")
    completion_token = completion.get("token")
    if not isinstance(completion_path, str) or not isinstance(completion_token, str):
        raise RuntimeError("Kommodo returned an invalid upload completion grant")

    last_provider_error: BaseException | None = None
    for provider in providers:
        parts = provider.get("upload_parts") if isinstance(provider, dict) else None
        abort_url = provider.get("abort_url") if isinstance(provider, dict) else None

        try:
            if not isinstance(parts, list) or len(parts) != expected_parts:
                raise RuntimeError("Kommodo returned an invalid multipart part list")

            def put_part(index: int) -> dict[str, Any]:
                part = parts[index]
                if (
                    not isinstance(part, dict)
                    or part.get("part_number") != index + 1
                ):
                    raise RuntimeError("Kommodo returned unordered multipart parts")
                offset = index * chunk_size
                length = min(chunk_size, size - offset)
                with path.open("rb") as source:
                    source.seek(offset)
                    payload = source.read(length)
                if len(payload) != length:
                    raise OSError(f"Could not read upload part {index + 1}")

                last_error: BaseException | None = None
                for attempt in range(3):
                    try:
                        with _storage_request(
                            part["url"],
                            method="PUT",
                            data=payload,
                            content_type="application/octet-stream",
                        ) as response:
                            etag = response.headers.get("ETag")
                        if not etag:
                            raise RuntimeError("Storage response omitted ETag")
                        return {"part_number": part["part_number"], "etag": etag}
                    except (OSError, RuntimeError, urllib.error.URLError) as error:
                        last_error = error
                        if attempt < 2:
                            time.sleep(attempt + 1)
                raise RuntimeError(f"Upload part {index + 1} failed") from last_error

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(concurrency, len(parts))
            ) as pool:
                completed_parts = []
                for completed_part in pool.map(put_part, range(len(parts))):
                    completed_parts.append(completed_part)
                    if on_progress:
                        on_progress(len(completed_parts), len(parts))

            complete_url = provider.get("complete_url")
            if not isinstance(complete_url, str):
                raise RuntimeError("Kommodo omitted the multipart completion URL")
            completion_xml = (
                "<CompleteMultipartUpload>"
                + "".join(
                    "<Part>"
                    f"<PartNumber>{part['part_number']}</PartNumber>"
                    f"<ETag>{escape(part['etag'])}</ETag>"
                    "</Part>"
                    for part in completed_parts
                )
                + "</CompleteMultipartUpload>"
            ).encode("utf-8")
            with _storage_request(
                complete_url,
                method="POST",
                data=completion_xml,
                content_type="application/xml",
            ):
                pass
            break
        except Exception as error:
            last_provider_error = error
            if isinstance(abort_url, str):
                try:
                    with _storage_request(abort_url, method="DELETE"):
                        pass
                except Exception:
                    pass
    else:
        raise RuntimeError("Every storage provider failed") from last_provider_error

    try:
        return _request(
            "POST",
            completion_path,
            body={"completion_token": completion_token},
            retry_server_errors=True,
        )
    except KommodoAPIError as error:
        # A lost success response can make the safe retry return 409 because
        # the recording already exists. That is a completed finalization, not
        # a reason to upload the bytes again.
        if error.status == 409:
            return {
                "upload_id": initialized.get("upload_id"),
                "recording_id": initialized.get("recording_id"),
                "status": "processing",
            }
        raise


def upload_image(
    file_path: str | os.PathLike[str],
    *,
    width: int,
    height: int,
    title: str | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Upload a local image to the authenticated user's image library."""
    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"Image file not found: {path}")
    if width < 1 or height < 1:
        raise ValueError("width and height must be positive integers")
    if path.stat().st_size < 1:
        raise ValueError("Image file is empty")
    resolved_type = content_type or mimetypes.guess_type(path.name)[0]
    if resolved_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        raise ValueError(
            "content_type must be image/jpeg, image/png, image/gif, or image/webp"
        )

    initialized = _request(
        "POST",
        "/api/public/v2/uploads",
        body={
            "type": "image",
            "title": title or path.name,
            "size_bytes": path.stat().st_size,
            "content_type": resolved_type,
            "width": width,
            "height": height,
        },
    )
    upload = initialized.get("upload") if isinstance(initialized, dict) else None
    upload_url = upload.get("url") if isinstance(upload, dict) else None
    if not isinstance(upload_url, str):
        raise RuntimeError("Kommodo returned an invalid image upload contract")
    with _storage_request(
        upload_url,
        method="PUT",
        data=path.read_bytes(),
        content_type=resolved_type,
    ):
        pass
    return {
        "upload_id": initialized.get("upload_id"),
        "type": "image",
        "status": "uploaded",
    }


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


def _main() -> None:
    parser = argparse.ArgumentParser(description="Kommodo public API client")
    commands = parser.add_subparsers(dest="command", required=True)

    video = commands.add_parser("upload-video", help="Upload a local video")
    video.add_argument("file")
    video.add_argument("--title")
    video.add_argument("--concurrency", type=int, default=4)

    image = commands.add_parser("upload-image", help="Upload a local image")
    image.add_argument("file")
    image.add_argument("--width", type=int, required=True)
    image.add_argument("--height", type=int, required=True)
    image.add_argument("--title")
    image.add_argument("--content-type")

    args = parser.parse_args()
    if args.command == "upload-video":

        def show_progress(completed: int, total: int) -> None:
            print(
                f"\ruploaded {completed}/{total} parts",
                end="\n" if completed == total else "",
                file=sys.stderr,
                flush=True,
            )

        result = upload_video(
            args.file,
            title=args.title,
            concurrency=args.concurrency,
            on_progress=show_progress,
        )
    else:
        result = upload_image(
            args.file,
            width=args.width,
            height=args.height,
            title=args.title,
            content_type=args.content_type,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _main()
