# Kommodo Public API v2 — Developer Quickstart

Upload media; read recordings, transcripts, AI metadata, folders, and team members; update titles and create pages.

**Base URL:** `https://kommodo.ai/api/public/v2`
**Auth:** `Authorization: Bearer <token>`
**Request pacing:** Bearer-authenticated v2 calls include `X-RateLimit-Remaining` and `X-RateLimit-Reset`. On `429`, wait the `Retry-After` duration rather than hard-coding a quota.

---

## 1. Get a token

1. Sign in at https://kommodo.ai and open **Account → API**.
2. Click **Generate token**. Choose `read` (default) or `read+write` for uploads and other write operations.
3. Copy the token. Treat it like a password.

Tokens are user-scoped. Team visibility follows the same live account and group policy as the dashboard; a solo token sees only the user's own recordings.

---

## 2. The use case: nightly transcript sync

The pattern: keep a watermark of the last `created_at` you successfully processed, page forward from there, fetch transcripts by ID.

```python
import os, time, requests
from datetime import datetime, timezone

BASE = "https://kommodo.ai/api/public/v2"
TOKEN = os.environ["KOMMODO_API_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

def list_recordings(since_iso: str):
    cursor = None
    while True:
        params = {"since": since_iso, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE}/recordings", headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for rec in data["recordings"]:
            yield rec
        cursor = data.get("next_cursor")
        if not cursor:
            return

def fetch_transcript(recording_id: str):
    r = requests.get(
        f"{BASE}/recordings/{recording_id}/transcript",
        headers=HEADERS,
        params={"format": "json"},
        timeout=60,
    )
    if r.status_code == 404:
        return None  # transcript not ready yet
    r.raise_for_status()
    return r.json()  # { "format": "json", "cues": [{ "start_seconds", "end_seconds", "text" }] }

def run(since_iso: str):
    latest = since_iso
    for rec in list_recordings(since_iso):
        transcript = fetch_transcript(rec["id"])
        if transcript is None:
            continue
        save(rec, transcript)              # your storage
        if rec["created_at"] > latest:
            latest = rec["created_at"]
    return latest                          # persist this for next run

if __name__ == "__main__":
    last = load_watermark() or "2025-01-01T00:00:00Z"
    new_watermark = run(last)
    save_watermark(new_watermark)
```

Run it under cron, GitHub Actions, or Cloud Scheduler. The same code works for daily and weekly cadences — just change how often you invoke it.

---

## 3. Endpoints used by the sync job

### `GET /recordings`

List recordings the token can see, newest first.

**Query parameters**

| Param        | Type    | Notes                                                                            |
| ------------ | ------- | -------------------------------------------------------------------------------- |
| `since`      | ISO8601 | Only recordings created on or after this timestamp.                              |
| `until`      | ISO8601 | Only recordings created on or before this timestamp.                             |
| `q`          | string  | Recording-title search only. Does not search transcripts or AI metadata.         |
| `folder_id`  | string  | Only recordings whose `parentId` matches. Caller must have access to the folder. |
| `member_id`  | string  | Filter to a team member visible under the caller's account policy.               |
| `has_page`   | bool    | `true` returns only recordings that have a published or draft page.              |
| `cursor`     | opaque  | Pass through `next_cursor` from the previous response.                           |
| `limit`      | int     | Default 20, max 100.                                                             |

For libraries over 1,000 recordings, combine `q` or `has_page` with a bounded
`since` / `until` window on solo accounts. A `folder_id` request can inspect
only the newest 1,000 recordings in that folder, even when dates are supplied.

**Response**

```json
{
  "recordings": [
    {
      "id": "abc123",
      "title": "Q2 standup",
      "created_at": "2026-04-26T15:42:11Z",
      "duration_seconds": 312,
      "owner": { "id": "uid_xyz", "name": "Jane", "email": "jane@acme.com" },
      "urls": {
        "page": "https://kommodo.ai/recordings/abc123",
        "video": "https://...m3u8",
        "poster": "https://...jpg"
      },
      "ai": {
        "tags": ["standup", "q2"],
        "suggested_title": "Q2 Engineering Standup",
        "detected_language": "en",
        "status": "ready"
      },
      "stats": { "views": 17 },
      "sharing": { "is_public": true, "has_passcode": false }
    }
  ],
  "next_cursor": "eyJwIjoyfQ=="
}
```

The list response intentionally **omits** `ai.summary`, `ai.chapters`, and `ai.action_items` to keep payloads small. Fetch them via the detail endpoint when you need them.

### `GET /recordings/:id`

Same envelope as the list, plus:

```json
{
  "ai": {
    "summary": "...",
    "chapters": [{ "timestamp_seconds": 0, "title": "Intro" }],
    "action_items": ["Ship API v2", "Write docs"]
  }
}
```

### `GET /recordings/:id/transcript?format=json|vtt`

- `format=json` (**default**) returns `{ "format": "json", "cues": [{ "start_seconds", "end_seconds", "text" }] }`.
- `format=vtt` returns `text/vtt` — original WebVTT cues.
- Any other value, or an omitted parameter, falls back to JSON.

Returns `404` if no transcript exists yet (still processing or transcription disabled). Returns `502` if the upstream transcript file is unreachable or malformed — retry with backoff. Hard cap of 5 MB; larger transcripts return `413`.

---

## 4. Other endpoints

| Method  | Path                                   | Purpose                                                                                                                                                   | Scope |
| ------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| GET     | `/folders`                             | List owned and directly shared folders. `parent_id` scopes a level; `member_id` requires visibility to that member.                                      | read  |
| POST    | `/folders`                             | Create under an owned `parent_id`, or under the caller's root when omitted.                                                                                | write |
| PATCH   | `/folders/:id`                         | Rename a folder the caller owns or has full access to.                                                                                                    | write |
| DELETE  | `/folders/:id`                         | Delete an empty non-root folder. Returns 409 instead of recursively deleting content.                                                                     | write |
| GET     | `/team/members`                        | List the account roster. **Account-owner only**; other callers get 403.                                                                                   | read  |
| PATCH   | `/recordings/:id`                      | Update `title`, `description`, `folder_id`.                                                                                                               | write |
| POST    | `/recordings/:id/pages`                | Create a page from a recording.                                                                                                                           | write |
| POST    | `/uploads`                             | Initialize an image or multipart-video upload.                                                                                                            | write |
| POST    | `/uploads/:upload_id/complete`         | Queue a completed multipart video for processing.                                                                                                         | write |

Write endpoints require a `read+write` scope token and apply the same access rules as the dashboard. Folder and recording changes require ownership or `fullAccess` at the relevant folder boundary. Folder creation is narrower: its parent must be owned by the token's user.

Folder writes are ordinary JSON requests:

```bash
# Create under your root (add an owned parent_id to create a subfolder)
curl -X POST -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Customer calls"}' \
  "https://kommodo.ai/api/public/v2/folders"

# Rename
curl -X PATCH -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Customer calls — archived"}' \
  "https://kommodo.ai/api/public/v2/folders/<folder-id>"

# Delete only after moving every child folder, recording, and guide
curl -X DELETE -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
  "https://kommodo.ai/api/public/v2/folders/<folder-id>"
```

---

## 5. Upload files

Upload is a language-neutral HTTP contract. You do not need Python, this repository, or a Kommodo SDK. The canonical [public upload guide](https://kommodo.ai/developer#uploads) documents the complete request and response schemas and provides tested cURL and dependency-free Node references.

The high-level flow is:

1. Initialize `POST /uploads` with a read+write Bearer token.
2. Send image or video bytes directly to the returned signed storage URLs **without** the Bearer token.
3. For video, retain each part response's `ETag`, POST ordered `CompleteMultipartUpload` XML to the selected provider's `complete_url`, then POST the returned `completion_token` to `/uploads/:upload_id/complete` with the Bearer token.
4. Poll `GET /recordings/:recording_id`. A temporary 404 is expected before processing creates the recording.

Runnable references, with no repository clone:

```bash
# Complete cURL + jq video uploader
curl -fsS https://kommodo.ai/api-examples/upload-video.sh -o upload-video.sh
less upload-video.sh
chmod +x upload-video.sh
export KOMMODO_API_TOKEN="<read+write token>"
./upload-video.sh /absolute/path/to/meeting.mp4

# Dependency-free Node 20+ video uploader
curl -fsS https://kommodo.ai/api-examples/upload-video.mjs -o upload-video.mjs
node upload-video.mjs /absolute/path/to/meeting.mp4
```

Videos upload directly to signed storage URLs in the returned chunk size, so large files do not pass through the dashboard service. Retry transient part failures. If one provider fails, call its `abort_url` and restart the whole file against the next provider. Finish before `expires_in_seconds`; otherwise initialize a new upload.

Images initialize through the same `POST /uploads` endpoint and then use the returned signed PUT URL. They do not require a separate completion call.

The Skill also includes an optional Python implementation for agent workflows:

```bash
python3 scripts/client.py upload-video /absolute/path/to/meeting.mp4
python3 scripts/client.py upload-image /absolute/path/to/diagram.png \
  --width 1600 --height 900
```

The API currently accepts local files only. Importing a Zoom, Dropbox, or other remote download URL is not supported.

### Direct initialization shapes

```json
{ "type": "video", "title": "meeting.mp4", "size_bytes": 104857600 }
```

```json
{
  "type": "image",
  "title": "diagram.png",
  "size_bytes": 240123,
  "content_type": "image/png",
  "width": 1600,
  "height": 900
}
```

Do not send API credentials to the signed storage URLs. Use the Bearer token only for Kommodo API calls.

---

## 6. Pagination

Cursors are opaque base64 strings. Treat them as black boxes:

1. Call without `cursor`.
2. If `next_cursor` is non-null, pass it back on the next request.
3. Stop when `next_cursor` is `null`.

For incremental syncs, combine `since` with cursor traversal — the cursor handles ordering within a window, `since` handles the window itself.

---

## 7. Errors

| Status | Meaning                                          | Action                                                                                             |
| ------ | ------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| 401    | Missing or invalid token.                        | Reissue the token at /account.                                                                     |
| 403    | Your token is read-only and the endpoint writes. | Generate a read+write token at /account.                                                           |
| 403    | You do not have access to that recording.        | Check the id belongs to your account or a folder shared with you.                                  |
| 403    | Account policy or scope denies the operation.    | Not retryable without changing the token scope or account access.                                  |
| 404    | Recording or transcript not found.               | Skip and continue. Transcripts are not ready instantly.                                            |
| 413    | Transcript exceeds 5 MB.                         | No transcript URL is exposed. Fall back to `GET /recordings/:id` and use `ai.summary`.             |
| 409    | Folder is not empty.                             | Move child folders, recordings, guides, and restorable items in trash first. Do not recursively delete them. |
| 429    | Request pacing limit reached.                    | Sleep `Retry-After` seconds. `X-RateLimit-Reset` is **seconds remaining**, not an epoch timestamp. |
| 502    | Upstream transcript unreachable / malformed.     | Retry with exponential backoff. Skip the recording after 3.                                        |
| 5xx    | Other server error.                              | Retry with exponential backoff (start at 1s, cap at 60s).                                          |

`GET /team/members` is restricted to account owners. The `member_id` parameter on
`GET /folders` requires that the account visibility policy expose that member. Retrying
will not help when the policy denies the request.

A pragmatic retry policy for cron jobs:

```python
def get(url, **kw):
    for attempt in range(5):
        r = requests.get(url, headers=HEADERS, timeout=30, **kw)
        if r.status_code == 429:
            # Retry-After and X-RateLimit-Reset are both durations in seconds,
            # NOT epoch timestamps. Subtracting time.time() here would yield a
            # large negative number and produce a 1-second hot retry loop.
            wait = int(r.headers.get("Retry-After")
                       or r.headers.get("X-RateLimit-Reset")
                       or 60)
            time.sleep(max(1, wait))
            continue
        if 500 <= r.status_code < 600:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"giving up on {url}")
```

---

## 8. Curl reference

```bash
# List the 5 most recent recordings
curl -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
  "https://kommodo.ai/api/public/v2/recordings?limit=5"

# Recordings since a watermark
curl -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
  "https://kommodo.ai/api/public/v2/recordings?since=2026-04-20T00:00:00Z"

# Detail
curl -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
  "https://kommodo.ai/api/public/v2/recordings/<id>"

# Transcript as JSON
curl -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
  "https://kommodo.ai/api/public/v2/recordings/<id>/transcript?format=json"

# Update title (write scope)
curl -X PATCH -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"New title"}' \
  "https://kommodo.ai/api/public/v2/recordings/<id>"
```

Video upload is not a single request. Use the complete cURL reference in [Upload files](#5-upload-files), the dependency-free Node reference, or implement the documented multipart contract in your language.

---

## 9. Using the API from an AI agent

This document lives inside the Kommodo skill repo. If you use Claude Code, Cursor, Codex, or any agent that supports skills, install the skill instead of writing a client:

```bash
git clone https://github.com/kommodoai/kommodo-skill.git
./kommodo-skill/install.sh
```

The skill wraps the same endpoints with tool definitions for uploads, recording retrieval and updates, Page creation, folder listing and writes, and team-member lookup. Auth is handled via `KOMMODO_API_TOKEN` in your environment. See `SKILL.md` in this repo for the full tool reference.

---

## 10. Support

- Token issues, scope upgrades, premium activation: support@kommodo.ai
- API bug reports: include the request URL, the response status, and the timestamp in UTC. We will correlate from server logs.
