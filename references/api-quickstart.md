# Kommodo Public API v2 — Developer Quickstart

Upload media; read recordings, transcripts, AI metadata, folders, and team members; update titles and create pages.

**Base URL:** `https://kommodo.ai/api/public/v2`
**Auth:** `Authorization: Bearer <token>`
**Rate limit:** 1000 requests/hour, 100 concurrent per token. Headers `X-RateLimit-Remaining` and `X-RateLimit-Reset` are returned on every response.

---

## 1. Get a token

1. Sign in at https://kommodo.ai and open **Account → API**.
2. Click **Generate token**. Choose `read` (default) or `read+write` for uploads and other write operations.
3. Copy the token. It is shown once. Treat it like a password.

Tokens are user-scoped. A team-owner token with an active premium subscription sees all team members' recordings; a solo token sees only the user's own recordings.

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
| `q`          | string  | Substring match on title.                                                        |
| `folder_id`  | string  | Only recordings whose `parentId` matches. Caller must have access to the folder. |
| `member_id`  | string  | Filter to a single team member. Team-owner + premium tokens only.                |
| `has_page`   | bool    | `true` returns only recordings that have a published or draft page.              |
| `cursor`     | opaque  | Pass through `next_cursor` from the previous response.                           |
| `limit`      | int     | Default 20, max 100.                                                             |

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
| GET     | `/folders`                             | List folders. `parent_id` to scope. `member_id` requires a **team-owner token with active premium** — a solo caller must omit it, even for their own uid. | read  |
| GET     | `/team/members`                        | List team members. **Team-owner + active premium only**; every other caller gets 403.                                                                     | read  |
| PATCH   | `/recordings/:id`                      | Update `title`, `description`, `folder_id`.                                                                                                               | write |
| POST    | `/recordings/:id/pages`                | Create a page from a recording.                                                                                                                           | write |
| POST    | `/uploads`                             | Initialize an image or multipart-video upload.                                                                                                            | write |
| POST    | `/uploads/:upload_id/complete`         | Queue a completed multipart video for processing.                                                                                                         | write |

Write endpoints require a `read+write` scope token and apply the same access rules as the dashboard (owner, or `fullAccess` on the parent folder).

---

## 5. Upload files

Clone this repository and use the maintained client so your integration does not have to implement signed multipart storage requests:

```bash
git clone https://github.com/kommodoai/kommodo-skill.git
cd kommodo-skill
export KOMMODO_API_TOKEN="..."

# Local video; returns upload_id, recording_id, and status=processing
python3 scripts/client.py upload-video /absolute/path/to/meeting.mp4

# Local image; dimensions are part of the API contract
python3 scripts/client.py upload-image /absolute/path/to/diagram.png \
  --width 1600 --height 900
```

Videos upload directly to signed storage URLs in 5 MiB parts, so large files do not pass through the dashboard service. The client initializes `POST /uploads`, uploads and completes one returned storage provider, then calls the returned `/complete` URL. Processing is asynchronous: poll `GET /recordings/:recording_id`; a temporary 404 is expected before the recording is created.

Images initialize through the same `POST /uploads` endpoint and then use the returned signed PUT URL. They do not require a separate completion call.

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
| 403    | Team-owner endpoint. See below.                  | Not retryable.                                                                                     |
| 404    | Recording or transcript not found.               | Skip and continue. Transcripts are not ready instantly.                                            |
| 413    | Transcript exceeds 5 MB.                         | No transcript URL is exposed. Fall back to `GET /recordings/:id` and use `ai.summary`.             |
| 429    | Rate limit exceeded.                             | Sleep `Retry-After` seconds. `X-RateLimit-Reset` is **seconds remaining**, not an epoch timestamp. |
| 502    | Upstream transcript unreachable / malformed.     | Retry with exponential backoff. Skip the recording after 3.                                        |
| 5xx    | Other server error.                              | Retry with exponential backoff (start at 1s, cap at 60s).                                          |

Two endpoints are restricted to team owners with an active premium subscription:
`GET /team/members`, and the `member_id` parameter on `GET /folders`. Every other caller
receives a 403. Retrying will not help — omit `member_id`, or skip `/team/members` and
pass a uid you already hold.

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

Video upload is not a single curl request. Use the maintained Python client shown in [Upload files](#5-upload-files), or implement the multipart response contract directly.

---

## 9. Using the API from an AI agent

This document lives inside the Kommodo skill repo. If you use Claude Code, Cursor, Codex, or any agent that supports skills, install the skill instead of writing a client:

```bash
git clone https://github.com/kommodoai/kommodo-skill.git
./kommodo-skill/install.sh
```

The skill wraps the same endpoints with tool definitions for `upload_video`, `upload_image`, `find_recordings`, `get_recording`, `get_transcript`, `update_recording`, `create_page`, `list_folders`, and `list_team_members`. Auth is handled via `KOMMODO_API_TOKEN` in your environment. See `SKILL.md` in this repo for the full tool reference.

---

## 10. Support

- Token issues, scope upgrades, premium activation: support@kommodo.ai
- API bug reports: include the request URL, the response status, and the timestamp in UTC. We will correlate from server logs.
