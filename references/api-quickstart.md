# Kommodo Public API v2 — Developer Quickstart

Upload media; read recordings, transcripts, AI metadata, folders, and team members; update recordings and create Pages.

**Base URL:** `https://kommodo.ai/api/public/v2`
**Auth:** `Authorization: Bearer <token>`
**Request pacing:** Bearer-authenticated v2 calls include `X-RateLimit-Remaining` and `X-RateLimit-Reset`. Account-level admission is shared across tokens and MCP; X-WorkLimit-Remaining reports weighted work. Work allowances can differ by account and include snapshots, change-feed replays and setup calls. Switching endpoints does not bypass an exhausted allowance: wait for Retry-After, then initialize sync once and use changes/webhooks instead of repeated library scans. On `429` or a temporary `503`, honor `Retry-After`. Do not retry `sync_not_enabled` until rollout.

---

## 1. Get a token

1. Sign in at https://kommodo.ai and open **Account → API**.
2. Click **Generate token**. Choose `read` (default) or `read+write` for uploads and other write operations.
3. Copy the token. Treat it like a password.

API access requires an active paid account billed through Stripe. Tokens are user-scoped. Team visibility follows the same live account and group policy as the dashboard; recordings in explicitly shared folders remain accessible when their author privacy and recording gates allow it.

For a local Bash or Zsh session, set the token without placing it in shell history:

```bash
printf 'Paste your Kommodo API token: '
IFS= read -r -s KOMMODO_API_TOKEN
export KOMMODO_API_TOKEN
printf '\n'
```

---

## 2. Import newly created recordings (creation filter only)

This example does not detect edits, deletions, or access changes. For an ongoing mirror use the staged sync protocol below. The creation-only pattern: keep a watermark of the last `created_at` you successfully processed, page forward from there, fetch transcripts by ID.

```python
import os, requests

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
    transcript_pending = False
    for rec in list_recordings(since_iso):
        transcript = fetch_transcript(rec["id"])
        if transcript is None:
            transcript_pending = True
            continue
        save(rec, transcript)              # idempotent/upsert by recording id
        if rec["created_at"] > latest:
            latest = rec["created_at"]
    # Do not advance past a recording whose transcript is not ready. This may
    # replay successful records on the next run, so save() must be idempotent.
    return since_iso if transcript_pending else latest

if __name__ == "__main__":
    last = load_watermark() or "2025-01-01T00:00:00Z"
    new_watermark = run(last)
    save_watermark(new_watermark)
```

Run it under cron, GitHub Actions, or Cloud Scheduler. The same code works for daily and weekly cadences — just change how often you invoke it.
`since` is inclusive, so the record at the watermark can appear again; deduplicate by recording id.

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

After rollout, new folder and solo title/Page-filter traversals use bounded
candidate pages. Keep all filters unchanged and continue through empty pages
when next_cursor is present. Old cursors retain their older 1,000-candidate
fallback behavior; restart without a cursor to use the new traversal.

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
| GET     | `/folders`                             | Paginated owned/shared folders. Supports `parent_id`, `member_id`, `cursor`, and `limit` (default 20, max 100).                                          | read  |
| POST    | `/folders`                             | Create under an owned `parent_id`, or under the caller's root when omitted.                                                                                | write |
| PATCH   | `/folders/:id`                         | Rename a folder the caller owns or has full access to.                                                                                                    | write |
| DELETE  | `/folders/:id`                         | Delete an empty non-root folder. Returns 409 instead of recursively deleting content.                                                                     | write |
| GET     | `/team/members`                        | List the account roster. **Account-owner only**; other callers get 403.                                                                                   | read  |
| PATCH   | `/recordings/:id`                      | Update `title`, `description`, `tags`, or `folder_id`.                                                                                                    | write |
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

Recording updates accept `title` (up to 500 characters), `description` (up to 5,000), `tags` (up to 32 non-empty strings of 64 characters each), and `folder_id`. Send `folder_id: null` to remove a recording from its folder.

`POST /recordings/:id/pages` creates a draft unless `publish: true` is sent. Its response contains the Page id and published state, but not a public or custom-domain URL. Fetch `GET /recordings/:id` after creation for the canonical `urls.page`; the API does not currently return a custom-domain URL.

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
./upload-video.sh /absolute/path/to/meeting.mp4

# Dependency-free Node 20+ video uploader
curl -fsS https://kommodo.ai/api-examples/upload-video.mjs -o upload-video.mjs
node upload-video.mjs /absolute/path/to/meeting.mp4
```

Videos upload directly to signed storage URLs in the returned chunk size, so large files do not pass through the dashboard service. Retry transient part failures. If one provider fails, call its `abort_url` and restart the whole file against the next provider. Finish before `expires_in_seconds`; otherwise initialize a new upload.

Images initialize through the same `POST /uploads` endpoint and then use the returned signed PUT URL. They do not require a separate completion call.

The skill repository also includes an optional Python 3.9+ implementation for agent workflows:

```bash
python3 scripts/client.py upload-video /absolute/path/to/meeting.mp4
python3 scripts/client.py upload-image /absolute/path/to/diagram.png \
  --width 1600 --height 900
```

The API does not accept a Zoom, Dropbox, or other source URL directly. A custom integration can download or stream the remote file itself, then upload those bytes through this protocol.

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

Cursors are opaque strings; do not decode, edit or construct them. Treat them as black boxes:

1. Call without `cursor`.
2. If `next_cursor` is non-null, pass it back on the next request.
3. Stop when `next_cursor` is `null`.

For creation-only imports, combine `since` with cursor traversal. `since` filters creation time, so it cannot detect later edits or deletions. Keep all filters unchanged between pages. An empty page is not terminal if next_cursor is present.

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
| 409    | A Page or completed upload already exists.       | Do not create a duplicate. Fetch the recording state instead.                                      |
| 429    | Request pacing limit reached.                    | Sleep `Retry-After` seconds. `X-RateLimit-Reset` is **seconds remaining**, not an epoch timestamp. |
| 502    | Transcript or upload service temporarily failed. | Retry with exponential backoff.                                                                    |
| 503    | Billing verification is temporarily unavailable. | Wait for `Retry-After`, then retry.                                                                 |
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

For Claude Code, install the skill so the agent can load the API instructions and bundled helper:

```bash
git clone https://github.com/kommodoai/kommodo-skill.git
./kommodo-skill/install.sh
```

The skill describes the same endpoints and includes Python helper functions for uploads, recording retrieval and updates, Page creation, folder listing and writes, and team-member lookup. Auth is handled via `KOMMODO_API_TOKEN` in your environment. See `SKILL.md` in this repo for the helper reference.

Other agents and applications should call the same HTTP API directly. Agent Skills-compatible products may also load `SKILL.md`, but their installation path is product-specific; the included installer targets Claude Code.

---

## 10. Support

- Token issues, scope upgrades, premium activation: support@kommodo.ai
- API bug reports: include the request URL, the response status, and the timestamp in UTC. We will correlate from server logs.


## Incremental sync and webhooks (staged rollout)

These endpoints require the sync feature to be enabled on the deployment.
`503` with `code: sync_not_enabled` means unavailable; do not loop retries.
Existing list/detail/transcript calls continue to work independently.

All paths below are relative to `/api/public/v2` and accept the same Bearer token.

| Method | Path | Scope | Result |
| --- | --- | --- | --- |
| POST | /sync/checkpoints | read | Idempotently enroll; pending (202) or ready (200) |
| GET | /sync/status | read | pending with retry_after_seconds, or ready with checkpoint |
| GET | /sync/snapshot?checkpoint=…&cursor=…&limit=100 | read | Bounded baseline or reconciliation page |
| GET | /changes?cursor=…&limit=100 | read | Minimal changes and next_cursor/checkpoint |
| GET | /webhook-subscriptions | read | subscriptions with id, url, state, last_delivery_status |
| POST | /webhook-subscriptions | write | `{url: "https://…"}`; subscription plus signing_secret once |
| DELETE | /webhook-subscriptions/{id} | write | Disable/remove caller's destination (204) |

1. Open a checkpoint. While pending, wait retry_after_seconds and inspect status.
2. Save the first ready checkpoint **before** the baseline. Page snapshot with
   that checkpoint until complete. Items have resource_type, resource, scope_ref
   and source_refs. Page-level fields include scope_ref, scope_complete,
   removed_scopes, complete and next_cursor. Empty pages can have continuation.
3. Track associations by opaque source key. Stage all pages of a source until
   scope_complete, then replace only that source's associations. Apply
   removed_scopes; remove a local resource only when no valid source remains.
   Shared folders and team access can overlap. Do not treat a scoped snapshot
   as a replacement of the entire library.
4. Replay changes from the **initial** checkpoint to catch changes during the
   baseline. Apply each batch idempotently before saving its continuation.
   Folder upserts also include the current folder object in resource; apply it directly (there is no folder-detail GET). resource_version is opaque; changed identifies metadata, media, transcript,
   ai, folder, page_ref or sharing changes. An upsert includes resource_id and
   the complete current source_refs; replace that resource's associations and
   fetch only the required detail/transcript. A delete removes the local item.
5. For operation reconcile, page snapshot with checkpoint=reconcile_ref, applying
   its removals and source replacements before advancing the change cursor.
6. Follow next_cursor while non-null; otherwise persist checkpoint. Quiet reads
   return an empty array and a renewed checkpoint. Honor errors and pacing.

History/checkpoints last up to 30 days; scope activity is renewed by normal feed
reads. `410 resync_required` means open a new checkpoint and rebuild the baseline.
`409 sync_pending` means enrollment or reconciliation is pending: wait before
retrying. Wrong-user/tampered cursors return 400. Visibility is checked against
current authority. The feed converges to current state, not a full immutable
history of every intermediate edit. A resource can change again before hydration.
View counters, processing progress and owner display-profile edits do not emit
feed events. Fetch recording detail when you need current view counts.

### Webhook receiver

Dashboard users can create/list/remove destinations in Account → API using their
signed-in session, without replacing a read-only integration token. HTTP creation
and removal require write scope. Five destinations per user are allowed. Remove
and recreate paused destinations after correcting the receiver.

Creation returns signing_secret once. Store the exact returned string as the
UTF-8 HMAC key (do not base64-decode it). Do not print it into an agent transcript.
The first verification is scheduled no sooner than 30 seconds after creation.
For every request, validate its timestamp (e.g. ±300 seconds) and verify:

```
Kommodo-Webhook-Signature = v1=<hex HMAC-SHA256(secret, timestamp + "." + raw_body)>
```

Use the exact `Kommodo-Webhook-Timestamp` header string and unmodified body bytes;
compare signatures in constant time before parsing or doing work. Do not reuse
the API Bearer token as the secret. For a subscription.verify payload containing
subscription_id and nonce, return 2xx JSON within five seconds:

```
{"challenge_response": "<hex HMAC-SHA256(secret, 'verify.' + subscription_id + '.' + nonce)>"}
```

An active receiver gets sync.available events (id, type, subscription_id,
created_at, version), without recording content. Persist a deduplicated wake-up
job keyed by Kommodo-Webhook-Id, respond 2xx quickly, then replay changes from
your durable saved checkpoint. Notifications coalesce changes for at least 30
seconds; scheduler and queue latency apply. Delivery can repeat. Keep occasional
feed checks as recovery, rather than re-crawling every folder and transcript.

The receiver must be public HTTPS. Private/reserved addresses and redirects are
blocked at connection time. Responses are capped at 4 KiB, timeout is five
seconds, and a notification has at most five reserved attempt slots within
24 hours. Provider/secret failures or a crashed worker can consume a slot without
an HTTP request. Backoff is 1 minute, 5 minutes, 30 minutes and 2 hours; bounded
Retry-After is respected. Exhaustion pauses the subscription; inspect status.

### Transcript revalidation

Immutable transcript artifacts support format-specific ETags. Store body plus
ETag, then send If-None-Match on the same format. On 304, use your stored body.
Authorization and account admission still run on each request. A 304 saves
transfer/parsing work; it is not a zero-read request. Legacy mutable references
may have no ETag. The bundled Python get_transcript helper does not cache or
handle conditional responses; use your HTTP library if you need this feature.


### Actionable quota failures

A 429 retains the error message and adds code, retry_after_seconds, guidance and
documentation_url. Retry-After remains the pacing authority. Guidance recommends
the change feed/webhooks only when enabled; an unavailable deployment recommends
pacing scans and reusing previously fetched results. MCP and the Python helper
surface these instructions. The helper retries 429 once only when the complete
Retry-After wait is at most 60 seconds; longer waits are returned as errors for
your scheduler to handle, without an early retry. These messages do not change
the configured quota or automatically block every repeating crawl.
