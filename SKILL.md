---
name: kommodo
description: >
  Work with Kommodo recordings via the public v2 API. Search/filter the user's
  team recordings, upload local videos or images, read AI-generated summaries
  and transcripts, manage folders, update recordings, and create draft or
  published Pages. Invoke
  when the user asks about their Kommodo videos, asks to summarize work done
  on a project from recordings, or asks to rename/retag/publish a recording.
---

# Kommodo v2 API skill

## When to invoke

Trigger this skill when the user asks to:

- **Find recordings** by creation date, title, folder, team member, or whether they already have a Page; filter returned `ai.tags` client-side when needed
- **Read a transcript** or **summarize work** captured in one or more recordings (for weekly digests, per-client reports, retrospective notes)
- **Create, rename, or delete an empty folder**, and move recordings between folders
- **Rename or retag** a recording
- **Create a draft or published Page** from a recording
- **Upload a local video or image** into the user's Kommodo account
- Answer questions like "what did we do for Client X this week?" by aggregating recording metadata

Do NOT invoke this skill for: account billing, admin console actions, importing media from a remote URL, video editing, or deleting recordings (folder deletion does not delete recordings).

## Authentication

All Kommodo API requests require a Bearer token issued from `https://kommodo.ai/account?tab=api` (or the equivalent dev URL). Signed storage URLs returned during upload must be called without the Bearer token.
API tokens require an active paid account billed through Stripe.

Scopes:

- **read** — default. List, detail, transcript, folders, team members.
- **read+write** — upload, folder changes, rename, retag, move, create-page. Issued explicitly via the UI's "Read + write" toggle.

Store the token in `KOMMODO_API_TOKEN` env var. The scripts in `scripts/client.py` read this at call time; never interpolate the token into a prompt.

## Helper functions

The HTTP API is the contract. `scripts/client.py` provides dependency-free Python helpers that return native dicts for composition by the agent. In Claude Code, reference the bundled client as `${CLAUDE_SKILL_DIR}/scripts/client.py`; do not assume the current working directory is the skill directory.

Execute a helper through the shell and emit JSON, for example:

```bash
PYTHONPATH="${CLAUDE_SKILL_DIR}/scripts" python3 - <<'PY'
import json
from client import find_recordings
print(json.dumps(find_recordings(limit=5)))
PY
```

The helper reads the token from the process environment; never place the token in the command or generated Python.

### Read

1. `find_recordings(query=None, since=None, until=None, folder_id=None, member_id=None, has_page=None, limit=20, cursor=None)` — **the workhorse.** Searches recording titles in the user's visible recordings, newest first. It does not search transcripts, summaries, tags, or suggested titles. Returns `{recordings: [...], next_cursor: ...}`. Each recording envelope includes `ai.tags`, `ai.suggested_title`, `ai.detected_language`, `ai.status`, plus `page` and `folder` when applicable. Heavy AI fields (`summary`, `chapters`, `action_items`) are **list-omitted** for payload size — fetch via `get_recording` when needed.

2. `get_recording(id)` — full envelope including `ai.summary`, `ai.chapters` (`timestamp_seconds` + title), `ai.action_items`. Use when the summary alone is enough context; skip if the user needs the raw transcript.

3. `get_transcript(id, format='json')` — server-proxied; `format='json'` returns `{cues: [{start_seconds, end_seconds, text}]}`, `format='vtt'` returns raw WEBVTT. JSON is preferred for reasoning; VTT is for captions UI. The Python client does not cache responses.

4. `list_folders(parent_id=None, member_id=None, cursor=None, limit=20)` — folders the caller owns or has direct access to. Pass `member_id` only when the account visibility policy lets the caller browse that member. `limit` accepts 1–100.

5. `list_team_members()` — needed to resolve "who"-filters (`member_id` takes a uid).
   **Account-owner only.** Returns 403 when the token cannot view the account roster.

   **`member_id` is not uniform across helpers — do not generalise:**

   | Helper | Solo caller passing their **own** uid | Any other uid |
   | --- | --- | --- |
   | `find_recordings(member_id=…)` | Allowed (no-op filter) | 403 |
   | `list_folders(member_id=…)` | **403** — no own-uid exemption | Allowed only when that member is visible under the account policy |

   So a solo caller must **omit `member_id` entirely** on `list_folders`. Passing your own
   uid there fails even though the same value works on `find_recordings`.

### Write — require read+write scoped token

6. `update_recording(id, title=None, description=None, tags=None, folder_id=None)` — patch one or more fields. Pass `None` to skip a Python argument; pass `''` or `[]` to clear text/tags. In the helper, `folder_id=""` removes the recording from its folder. Direct HTTP callers may send `folder_id: null` for the same result.

7. `create_folder(name, parent_id=None)` — create below an owned `parent_id`, or below the caller's root when omitted. Creating inside someone else's shared folder is not supported.

8. `rename_folder(id, name)` — rename a folder the caller owns or has full access to.

9. `delete_folder(id)` — delete an empty, non-root folder. A folder with child folders, recordings, guides, or restorable items in trash returns 409; move them first. Never interpret this as permission to delete its contents.

10. `create_page(recording_id, headline=None, description=None, publish=False, template_id=None)` — converts a recording into a page (draft by default). Returns `{page: {id, published, template_id}, recording_id}`. The response does not include a custom-domain URL; fetch the recording envelope for its canonical Kommodo URL. `template_id` is stored as metadata, but the API currently creates the default layout. Fails with 409 if the recording already has a page.

11. `upload_video(file_path, title=None, concurrency=4)` — uploads a local video directly to Kommodo storage using the API's multipart contract, then queues processing. Returns `{upload_id, recording_id, status: "processing"}`. The path must refer to a file the user has authorized the agent to upload.

12. `upload_image(file_path, width, height, title=None, content_type=None)` — uploads a local JPEG, PNG, GIF, or WebP to the user's image library. Width and height are required by the API; determine them from the source image rather than guessing.

## Composition patterns (read this before multi-step work)

**Summarize a project's recent work:**

```
find_recordings(folder_id="...", since=one_week_ago)
  → for each: if ai.status == 'ready', use ai.suggested_title + call get_recording(id) for ai.summary
  → compose a digest from those summaries in the agent's own context
```

Do NOT call `get_transcript` unless the summary is insufficient; transcripts are token-heavy.

**Retag + rename in one pass:**

```
update_recording(id, title="New Title", tags=["client-x", "standup"])
```

Both fields update atomically.

**Detect new recordings since the last poll:**

```
find_recordings(since=last_poll_timestamp_iso, cursor=None) then loop cursor until next_cursor is None
```

`since` filters `created_at`, not `updated_at`; this is not a metadata-change feed. Store the highest `created_at` seen and use it as `since` next time.
The lower bound is inclusive, so deduplicate by recording id. Do not advance a transcript-sync watermark past a recording whose transcript is not ready.

**Upload a local video:**

```
upload_video("/absolute/path/to/meeting.mp4")
  → report the returned recording_id and that processing continues asynchronously
  → use get_recording(recording_id) later; 404 is expected until processing creates the recording
```

The upload helpers accept local files only. The API has no source-URL import parameter; a custom integration may download or stream remote media itself, then upload those bytes through the same protocol.

## Errors

| Code | Meaning                                               | Action                                                                                                        |
| ---- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| 401  | Token missing/invalid                                 | Re-prompt user for a fresh token                                                                              |
| 403  | Read-only token used on a write endpoint              | Ask the user for a read+write token.                                                                          |
| 403  | No permission to modify this recording                | State that access was denied and stop; do not guess the owner.                                                 |
| 403  | `list_team_members`, or unavailable `member_id`       | The account policy does not expose that roster/member. Tell the user, and do not retry.                       |
| 404  | Recording not found or not visible to caller          | Confirm the id; the user may not have access                                                                  |
| 409  | Recording already has a page (on `create_page`)       | Tell the user and stop — do not try to overwrite                                                              |
| 409  | Folder is not empty                                  | Move its child folders, recordings, and guides first; never recursively delete them.                          |
| 413  | Transcript exceeds 5 MB                               | Rare; recording is unusually long. Fall back to `get_recording` summary instead.                              |
| 429  | Request pacing limit reached                          | Honor `Retry-After` and `X-RateLimit-Reset`. Do not hard-code a quota.                                        |
| 502  | Transcript or upload service temporarily unavailable | Transient — retry with backoff.                                                                               |
| 503  | Billing verification temporarily unavailable          | Wait for `Retry-After`, then retry.                                                                            |

## Guardrails

- **Never present an ID field as prose.** When referring to a recording, include its `title` first and the `id` parenthetical.
- **Never expose the transcript URL in output** — it's auth-gated. Either summarize or quote cues with timestamps.
- **For write actions, always state what you will do before doing it** (`"I'm going to rename 'Sprint Review 4/12' to 'Sprint Review — Acme launch'; proceed?"`) and wait for user confirmation, unless the user has already explicitly given scope-specific instructions in the same turn.
- **Never upload an inferred file.** Use only the exact local path the user supplied or explicitly selected, and state the filename before starting the transfer.
- **Never send the Bearer token to a signed storage URL.** Use it only for the Kommodo API origin.
- **On 429, do not silently retry in a tight loop.** Wait the `Retry-After` seconds the server sends.

## References

- `references/filtering.md` — exact filter semantics and coverage limits.
- `references/recording-envelope.md` — response shapes.
- `references/worked-scenarios.md` — composition examples.
- `references/api-quickstart.md` — direct HTTP integration guide.
