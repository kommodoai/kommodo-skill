# kommodo-skill

A [Claude Code](https://docs.claude.com/en/docs/claude-code) skill that wraps the [Kommodo](https://kommodo.ai) public API. Ask the agent to upload a local video or for "recordings from last week on Acme" — it handles multipart uploads, search, AI summaries, transcripts, renaming, retagging, and publishing recordings as pages.

We are actively working on this API. Feedback is welcome! feedback@kommodo.ai 

---

## What you get

Nine named tools backed by the public API:

| Tool | What it does |
|---|---|
| `find_recordings` | Paginated list with filters (date range, title, team member, folder, has-page) |
| `get_recording` | Full envelope including AI summary, chapters, action items |
| `get_transcript` | JSON cues or WEBVTT, server-proxied |
| `list_folders` | Folders you own or have access to |
| `list_team_members` | Team owner + member list (team-owner-only) |
| `update_recording` | Rename, retag, update description, move folder (write scope) |
| `create_page` | Convert a recording into a published page (write scope) |
| `upload_video` | Multipart-upload a local video and queue processing (write scope) |
| `upload_image` | Upload a local image to the image library (write scope) |

See [`SKILL.md`](SKILL.md) for the full tool reference, [`references/`](references/) for response schemas and filter recipes.

Integrating without an agent (cron jobs, ETL, plain HTTP)? Start at [`references/api-quickstart.md`](references/api-quickstart.md) — Python sample for nightly transcript sync, full endpoint and error tables.

---

## Install (pick one)

### One-shot installer

```bash
git clone https://github.com/kommodoai/kommodo-skill.git
./kommodo-skill/install.sh
```

That symlinks `~/.claude/skills/kommodo` back to the cloned repo, so `git pull` upgrades the skill in place.

Variations:
```bash
./install.sh --copy                # copy files instead of symlink
./install.sh --project ./my-app    # install into my-app/.claude/skills/kommodo
./install.sh --help
```

### Manual

```bash
git clone https://github.com/kommodoai/kommodo-skill.git ~/kommodo-skill
mkdir -p ~/.claude/skills
ln -s ~/kommodo-skill ~/.claude/skills/kommodo
```

### Project-committed (shared with a team)

```bash
cd your-project
mkdir -p .claude/skills
git clone https://github.com/kommodoai/kommodo-skill.git .claude/skills/kommodo
git add .claude/skills/kommodo
git commit -m "Add Kommodo skill for Claude Code"
```

---

## Setup

1. **Generate an API token** at https://kommodo.ai/account?tab=api. Pick scope:
   - **Read** — search / summaries / transcripts
   - **Read + write** — plus file uploads, rename, retag, move, create-page

2. **Export it** in your shell profile:
   ```bash
   echo 'export KOMMODO_API_TOKEN="<paste from /account?tab=api>"' >> ~/.zshrc
   source ~/.zshrc
   ```

3. **Restart Claude Code** fully (quit, not just reload) so it picks up the new skill.

4. **Try it.** Ask Claude: *"List my five most recent Kommodo recordings."*

---

## Verify

No Claude Code handy? Call the client directly:

```bash
export KOMMODO_API_TOKEN="..."
python3 -c "
import sys
sys.path.insert(0, 'scripts')
from client import find_recordings
print(find_recordings(limit=3))
"
```

The Skill's Python adapter is optional. If you want it, it can upload a video
without an agent:

```bash
python3 scripts/client.py upload-video /absolute/path/to/meeting.mp4
```

The command returns the new `recording_id` with `status: processing`. Developers
using JavaScript, Go, Ruby, PHP, Java, cURL, or another HTTP client do not need
this repository. The canonical [public upload guide](https://kommodo.ai/developer#uploads)
documents the language-neutral protocol and provides complete cURL and Node
references.

---

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `KOMMODO_API_TOKEN` | yes | — | Bearer token from the account page |
| `KOMMODO_API_BASE_URL` | no | `https://kommodo.ai` | Override only if directed by Kommodo support |

---

## HTTP API direct

The skill is an optional wrapper — the HTTP API is the real contract:

```bash
curl -H "Authorization: Bearer $KOMMODO_API_TOKEN" \
     "https://kommodo.ai/api/public/v2/recordings?limit=5"
```

Public endpoint and upload reference: https://kommodo.ai/developer

Video upload is a sequence of standard HTTP requests because bytes go directly
to signed storage URLs in multiple parts. The public guide documents every
request and response, including retries, ETags, provider completion, aborts,
and Kommodo finalization. `scripts/client.py` is only one optional implementation.

---

## Upgrading

```bash
cd path/to/kommodo-skill && git pull
```

If you used `./install.sh --copy` instead of symlink, re-run the installer after pulling.

---

## Versioning

Tagged releases follow [semver](https://semver.org). Pin to a tag if you need stability:

```bash
git clone --branch v0.1.0 https://github.com/kommodoai/kommodo-skill.git
```

Breaking changes get a major bump. Additive changes (new tools, new optional params) are minor. Bug fixes are patch.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `KOMMODO_API_TOKEN not set` | env var missing | `export` it + restart your shell and Claude Code |
| `401 Unauthorized` | token expired or wrong environment | Regenerate at `/account?tab=api`. Verify `KOMMODO_API_BASE_URL` matches the token's environment. |
| `403 Write scope required` | Read-scoped token used for a write endpoint | Regenerate with the **Read + write** toggle |
| `429 Too Many Requests` | Hit 1000 req/hr or 100 concurrent per token | Honor `Retry-After` header, back off |
| Skill not detected | Claude Code didn't rescan `~/.claude/skills/` | Fully quit and relaunch |

---

## Contributing

Issues and PRs welcome at https://github.com/kommodoai/kommodo-skill/issues.

This repo is the source of truth for the skill artifact. The underlying API lives in the private dashboard repo; changes to the API surface land here when the API ships a corresponding release.

---

## License

MIT — see [LICENSE](LICENSE).
