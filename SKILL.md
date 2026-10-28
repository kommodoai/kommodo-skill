---
name: kommodo
description: >
  Work with Kommodo recordings via the public v2 API. Search/filter the user's
  team recordings, upload local videos or images, read AI-generated summaries
  and transcripts, rename or retag recordings, and convert a recording into a published page. Invoke
  when the user asks about their Kommodo videos, asks to summarize work done
  on a project from recordings, or asks to rename/retag/publish a recording.
---

# Kommodo v2 API skill

## When to invoke

Trigger this skill when the user asks to:

- **Find recordings** by date range, title, tag, folder, team member, or whether they already have a published page
- **Read a transcript** or **summarize work** captured in one or more recordings (for weekly digests, per-client reports, retrospective notes)
- **Rename or retag** a recording, or move it to a different folder
- **Create a published page** from a recording
- **Upload a local video or image** into the user's Kommodo account
- Answer questions like "what did we do for Client X this week?" by aggregating recording metadata

Do NOT invoke this skill for: account billing, admin console actions, importing media from a remote URL, video editing, deleting recordings (no delete endpoint yet).

## Authentication

All requests require a Bearer token issued from `https://kommodo.ai/account?tab=api` (or the equivalent dev URL).

Scopes:

- **read** — default. List, detail, transcript, folders, team members.
- **read+write** — upload, rename, retag, move, create-page. Issued explicitly via the UI's "Read + write" toggle.

Store the token in `KOMMODO_API_TOKEN` env var. The scripts in `scripts/client.py` read this at call time; never interpolate the token into a prompt.

## The 9 tools

All tools are thin Python wrappers around the HTTP API in `scripts/client.py`. They return native Python dicts for composition by the agent. Full response schemas are in `references/recording-envelope.md`.

### Read

1. `find_recordings(query=None, since=None, until=None, folder_id=None, member_id=None, has_page=None, limit=20, cursor=None)` — **the workhorse.** Searches the user's visible team recordings. Returns `{recordings: [...], next_cursor: ...}`. Each recording envelope includes `ai.tags`, `ai.suggested_title`, `ai.detected_language`, `ai.status`, `page.published`, and `folder` (when applicable). Heavy AI fields (`summary`, `chapters`, `action_items`) are **list-omitted** for payload size — fetch via `get_recording` when needed.

2. `get_recording(id)` — full envelope including `ai.summary`, `ai.chapters` (`timestamp_seconds` + title), `ai.action_items`. Use when the summary alone is enough context; skip if the user needs the raw transcript.

3. `get_transcript(id, format='json')` — server-proxied; `format='json'` returns `{cues: [{start_seconds, end_seconds, text}]}`, `format='vtt'` returns raw WEBVTT. JSON is preferred for RAG / reasoning; VTT is for captions UI. Responses are cached client-side for 1 hour (content is immutable per recording).

4. `list_folders(parent_id=None, member_id=None, cursor=None)` — folders the caller owns or has access to. Pass `member_id` (team-owner + premium tokens only) to list folders owned by a specific team member instead.

5. `list_team_members()` — needed to resolve "who"-filters (`member_id` takes a uid).
   **Team-owner + premium only.** Returns 403 for any caller who is not the team owner,
   and 403 again if the owner lacks an active premium subscription.

   **`member_id` is not uniform across tools — do not generalise:**

   | Tool | Solo caller passing their **own** uid | Any other uid |
   | --- | --- | --- |
   | `find_recordings(member_id=…)` | Allowed (no-op filter) | 403 |
   | `list_folders(member_id=…)` | **403** — no own-uid exemption | 403 |

   So a solo caller must **omit `member_id` entirely** on `list_folders`. Passing your own
   uid there fails even though the same value works on `find_recordings`.

### Write — require read+write scoped token

6. `update_recording(id, title=None, description=None, tags=None, folder_id=None)` — patch one or more fields. Pass `None` to skip a field; pass `''` or `[]` to clear. `folder_id=None` means "don't touch"; `folder_id=""` or `folder_id=null` in the JSON means "remove from folder".

7. `create_page(recording_id, headline=None, description=None, publish=False, template_id=None)` — converts a recording into a page (draft by default). Returns `{page: {id, published, template_id}, recording_id}`. Fails with 409 if the recording already has a page.

8. `upload_video(file_path, title=None, concurrency=4)` — uploads a local video directly to Kommodo storage using the API's multipart contract, then queues processing. Returns `{upload_id, recording_id, status: "processing"}`. The path must refer to a file the user has authorized the agent to upload.

9. `upload_image(file_path, width, height, title=None, content_type=None)` — uploads a local JPEG, PNG, GIF, or WebP to the user's image library. Width and height are required by the API; determine them from the source image rather than guessing.

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

**Detect "what changed since last poll":**

```
find_recordings(since=last_poll_timestamp_iso, cursor=None) then loop cursor until next_cursor is None
```

Envelopes include `created_at` and `updated_at`. Store the highest `created_at` seen, use it as `since` next time.

**Upload a local video:**

```
upload_video("/absolute/path/to/meeting.mp4")
  → report the returned recording_id and that processing continues asynchronously
  → use get_recording(recording_id) later; 404 is expected until processing creates the recording
```

The upload tools accept local files only. They do not download Zoom, Dropbox, or other remote URLs.

## Errors

| Code | Meaning                                               | Action                                                                                                        |
| ---- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| 401  | Token missing/invalid                                 | Re-prompt user for a fresh token                                                                              |
| 403  | Read-only token used on a write endpoint              | Ask the user for a read+write token.                                                                          |
| 403  | No permission to modify this recording                | Name the recording's owner and stop.                                                                          |
| 403  | `list_team_members`, or `member_id` on `list_folders` | Team owners with premium only. Tell the user, and do not retry.                                               |
| 404  | Recording not found or not visible to caller          | Confirm the id; the user may not have access                                                                  |
| 409  | Recording already has a page (on `create_page`)       | Tell the user and stop — do not try to overwrite                                                              |
| 413  | Transcript exceeds 5 MB                               | Rare; recording is unusually long. Fall back to `get_recording` summary instead.                              |
| 429  | Rate limit (1000/hr) or 100 concurrent exceeded       | Honor `Retry-After` header. Back off.                                                                         |
| 502  | Transcript upstream unavailable                       | Transient — retry once with backoff.                                                                          |

## Guardrails

- **Never present an ID field as prose.** When referring to a recording, include its `title` first and the `id` parenthetical.
- **Never expose the transcript URL in output** — it's auth-gated. Either summarize or quote cues with timestamps.
- **For write actions, always state what you will do before doing it** (`"I'm going to rename 'Sprint Review 4/12' to 'Sprint Review — Acme launch'; proceed?"`) and wait for user confirmation, unless the user has already explicitly given scope-specific instructions in the same turn.
- **Never upload an inferred file.** Use only the exact local path the user supplied or explicitly selected, and state the filename before starting the transfer.
- **On 429, do not silently retry in a tight loop.** Wait the `Retry-After` seconds the server sends.
