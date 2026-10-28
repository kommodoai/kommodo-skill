# Filter cookbook

Use this as a jumping-off point when translating a user's question into a
`find_recordings` call.

## Supported filters (Phase 1)

| Param         | Shape      | Notes                                                                                                                                 |
| ------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `query` / `q` | string     | Title search only. It does not search transcripts, summaries, tags, or suggested titles.                                              |
| `since`       | ISO 8601   | Inclusive lower bound on `created_at`.                                                                                                |
| `until`       | ISO 8601   | Inclusive upper bound.                                                                                                                |
| `folder_id`   | string     | Only recordings whose `parentId` matches.                                                                                             |
| `member_id`   | uid        | Only recordings owned by this uid. Requires caller to have visibility to that member (team-owner token, or team-member on same team). |
| `has_page`    | bool       | `true` → only recordings with a page; `false` → only recordings without one.                                                          |
| `limit`       | int 1..100 | Default 20.                                                                                                                           |
| `cursor`      | opaque     | From previous response's `next_cursor`.                                                                                               |

For libraries over 1,000 recordings, use a bounded `since` / `until` window
when combining `query` or `has_page` on a solo account. A `folder_id` request
can inspect only the newest 1,000 recordings in that folder, even when dates
are supplied.

## Not supported in Phase 1 (filter client-side after `find_recordings`)

- **Filter by tag** — fetch envelopes, filter on `ai.tags`.
- **Filter by AI status** — fetch, filter on `ai.status == 'ready'`.
- **Filter by duration** — fetch, filter on `duration_seconds`.

## `list_folders` filters

| Param       | Shape  | Notes                                                                                                                |
| ----------- | ------ | -------------------------------------------------------------------------------------------------------------------- |
| `parent_id` | string | Folder children of this id. Pass `""` for root.                                                                      |
| `member_id` | uid    | Folders owned by this uid. The caller must have team-content visibility for that member. Useful for the team→member→folders walk. |
| `cursor`    | opaque | From previous `next_cursor`.                                                                                          |

## Recipes

### "What did we record last week for Acme?"

1. `list_folders()` → find the Acme folder id.
2. `find_recordings(folder_id="…", since=seven_days_ago)` → iterate cursor.
3. For each `ai.status == 'ready'` recording, call `get_recording(id)` for the summary.
4. Compose digest from the summaries.

### "Show me recordings by Alice from this month"

1. `list_team_members()` → find Alice's uid.
2. `find_recordings(member_id=alice_uid, since="2026-04-01T00:00:00Z")`.

### "What folders does Alice keep her work in?"

Discovering Alice through `list_team_members` requires the account-owner token.
Calling `list_folders(member_id=…)` requires team-content visibility for Alice.

1. `list_team_members()` → find Alice's uid.
2. `list_folders(member_id=alice_uid)` → her folders, owned-only.
3. Optional: pick one and `find_recordings(folder_id=…)` to drill in.

### "Which recordings still need an AI summary?"

1. `find_recordings(since=…)` over the date range.
2. Client-side filter `ai.status != 'ready'`.

### "Find my video about the Stripe migration"

1. `find_recordings(query="Stripe migration")` — searches recording titles.
2. Transcript full-text is **not** searchable today; if title doesn't match, expand date range and scan summaries via `get_recording`.

### "List recordings that already have a page published"

1. `find_recordings(has_page=true)`.
2. Client-side keep only `page.published == true`.

### "Find recordings created since the last run"

```
last_seen = stored_timestamp
batch = find_recordings(since=last_seen)
while batch.next_cursor:
    process(batch.recordings)
    batch = find_recordings(since=last_seen, cursor=batch.next_cursor)
process(batch.recordings)
stored_timestamp = max(r.created_at for r in all_batches)
```

`since` filters `created_at`; it is not a change feed. It will not return an
older recording merely because that recording was renamed, moved, or updated.
