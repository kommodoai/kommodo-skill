# Worked scenarios

End-to-end examples of how the helpers compose. Assume `KOMMODO_API_TOKEN`
is set. Run these snippets from the skill directory; all helper calls are via
`scripts/client.py`.

---

## 1. "Summarize Acme's work this week"

**User asks:** "What did we do for Client Acme last week?"

```python
from scripts.client import (
    find_recordings,
    get_recording,
    list_folders,
)
from datetime import datetime, timedelta, timezone

# 1. Find the Acme folder
folders = list_folders()["folders"]
acme = next((f for f in folders if f["name"].lower() == "acme"), None)
if not acme:
    raise RuntimeError("No folder named 'Acme'")

# 2. Pull recordings from the last 7 days
since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
recordings = []
cursor = None
while True:
    batch = find_recordings(folder_id=acme["id"], since=since, cursor=cursor)
    recordings.extend(batch["recordings"])
    cursor = batch["next_cursor"]
    if not cursor:
        break

# 3. For each ready recording, fetch full summary
digest_parts = []
for r in recordings:
    if r["ai"]["status"] != "ready":
        continue
    detail = get_recording(r["id"])
    summary = detail["ai"].get("summary", "")
    actions = detail["ai"].get("action_items", [])
    digest_parts.append({
        "title": r["title"],
        "when": r["created_at"][:10],
        "owner": r["owner"]["name"],
        "summary": summary,
        "actions": actions,
    })

# Agent then composes a human digest from digest_parts in its own context.
```

Do NOT fetch transcripts for this scenario — summaries are already AI-generated and suitable for a weekly digest.

---

## 2. "Rename yesterday's recording"

**User asks:** "Rename yesterday's standup recording to 'Standup — 2026-04-16' and move it to my Standups folder."

```python
from scripts.client import (
    find_recordings,
    list_folders,
    update_recording,
)

# 1. Locate the recording (e.g., by title search + creation-date window)
batch = find_recordings(query="standup", since="2026-04-16T00:00:00Z", until="2026-04-16T23:59:59Z")
candidates = batch["recordings"]
if len(candidates) != 1:
    # Show title, date, owner, and folder; ask the user to choose without
    # exposing internal ids.
    raise RuntimeError(f"Ambiguous: {len(candidates)} matches")

rec_id = candidates[0]["id"]

# 2. Find target folder
folders = list_folders()["folders"]
target = next(f for f in folders if f["name"] == "Standups")

# 3. Confirm with user BEFORE writing (skill guideline):
#    "I'll rename '<title>' to 'Standup — 2026-04-16' and move it to Standups. OK?"
# Then, on approval:
updated = update_recording(
    rec_id,
    title="Standup — 2026-04-16",
    folder_id=target["id"],
)
```

---

## 3. "Turn my Tuesday demo into a public page"

```python
from scripts.client import create_page, find_recordings, get_recording

# 1. Locate the demo recording
batch = find_recordings(query="demo", since="2026-04-15T00:00:00Z", until="2026-04-15T23:59:59Z")
rec = batch["recordings"][0]

# 2. Confirm with user, then create + publish atomically
result = create_page(
    rec["id"],
    headline="Kommodo Platform Demo — April 2026",
    publish=True,
)

# Keep result ids internal. Fetch the updated recording and report its title
# plus urls.page to the user.
```

Handle `KommodoAPIError.status == 409` by calling `get_recording(rec["id"])` and reporting the recording title plus its existing `urls.page`. Do not expose the Page id or try to create another Page.

---

## 4. "Find a video about the Stripe migration by Alice from last month"

```python
from scripts.client import (
    find_recordings,
    list_team_members,
)

team = list_team_members()["team"]
alice = next(m for m in team["members"] if m["name"].lower() == "alice")

batch = find_recordings(
    query="stripe migration",
    member_id=alice["id"],
    since="2026-03-01T00:00:00Z",
    until="2026-03-31T23:59:59Z",
)
```

If `batch["recordings"]` is empty, fall back to broader title search or scan summaries — transcript full-text search is not available in v2 Phase 1.

---

## 5. "Which recordings still need AI processing?"

```python
from scripts.client import iter_all_recordings

all_recs = iter_all_recordings(since="2026-04-01T00:00:00Z", limit=100)
pending = [r for r in all_recs if r["ai"]["status"] != "ready"]

# Report: count, oldest pending, plus per-owner grouping
```

`ai.status != 'ready'` catches both `pending` and `processing`. Today there's no explicit filter endpoint — client-side filtering is the only way.

---

## Composition rules (enforceable)

- **Use IDs only inside helper calls.** User-facing responses identify recordings by title and, when useful, date, owner, folder, or link. Show an ID only when the user explicitly requests it or is debugging an integration.
- **Always confirm before writes** (guideline in SKILL.md) unless the user has explicitly approved the action in the same turn.
- **Transcripts are expensive** — do not pull them unless the user's question genuinely requires the raw text (e.g., "find the moment where X was discussed"). Summaries + action items usually suffice.
- **Cursor until null** for incremental-sync scenarios. For ad-hoc queries, stop after the first page and say "showing the first 20 matches; ask me to continue" when `next_cursor` is present. The API does not return a total count.
