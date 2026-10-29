# kommodo-skill

A [Claude Code skill](https://code.claude.com/docs/en/skills) with optional Python helpers for the [Kommodo](https://kommodo.ai) public API. Ask Claude to upload a local video or for "recordings from last week on Acme" — the skill tells it how to use the API for uploads, title search, AI summaries, transcripts, folders, recording updates, and Pages.

We are actively working on this API. Feedback is welcome! feedback@kommodo.ai 

---

## What you get

The skill can use these helper functions from `scripts/client.py`:

| Helper | What it does |
|---|---|
| `find_recordings` | Paginated list with filters (date range, title, team member, folder, has-page) |
| `get_recording` | Full envelope including AI summary, chapters, action items |
| `get_transcript` | JSON cues or WEBVTT, server-proxied |
| `list_folders` | Folders you own or have access to |
| `create_folder` | Create a root child or subfolder (write scope) |
| `rename_folder` | Rename a folder (write scope) |
| `delete_folder` | Delete an empty non-root folder (write scope) |
| `list_team_members` | Team owner + member list (team-owner-only) |
| `update_recording` | Rename, retag, update description, move folder (write scope) |
| `create_page` | Create a draft Page, or publish it when requested (write scope) |
| `upload_video` | Multipart-upload a local video and queue processing (write scope) |
| `upload_image` | Upload a local image to the image library (write scope) |

See [`SKILL.md`](SKILL.md) for the full helper reference, [`references/`](references/) for response schemas and filter recipes.

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
./install.sh --force               # replace an existing installation
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
git clone https://github.com/kommodoai/kommodo-skill.git ../kommodo-skill
../kommodo-skill/install.sh --copy --project .
git add .claude/skills/kommodo
git commit -m "Add Kommodo skill for Claude Code"
```

This commits ordinary files, not a nested Git repository. Teammates receive the skill with the project.

The installer targets local Claude Code sessions. Cloud sessions do not read your local `~/.claude/skills`; enable the skill for your Claude account or add it to the cloud environment using that product's skill settings.

---

## Setup

1. **Generate an API token** at https://kommodo.ai/account?tab=api. API access requires an active paid account billed through Stripe. Pick scope:
   - **Read** — search / summaries / transcripts
   - **Read + write** — plus file uploads, folder changes, rename, retag, move, create-page

2. **Export it** before launching Claude Code. For a local terminal, read it without placing it in shell history:
   ```bash
   printf 'Paste your Kommodo API token: '
   IFS= read -r -s KOMMODO_API_TOKEN
   export KOMMODO_API_TOKEN
   printf '\n'
   ```

   This lasts for that terminal session. Claude Code Desktop and cloud sessions have separate environment settings; set `KOMMODO_API_TOKEN` there instead of assuming they inherit your terminal. Never commit the token.

3. **Start a new Claude Code session** so it inherits the token. If the skill directory was created while a session was already open and `/kommodo` is not listed, run `/reload-skills`.

4. **Try it.** Ask Claude: *"List my five most recent Kommodo recordings."*

---

## Verify

No Claude Code handy? From the cloned skill directory, call the client directly with Python 3.9 or newer:

```bash
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

The command returns the internal `recording_id` for programmatic polling with
`status: processing`; agents should describe the upload by filename/title and
link rather than exposing that id to the user. Developers
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

If you used `./install.sh --copy` instead of symlink, re-run the installer with `--copy --force` after pulling.

---

## Versioning

Tagged releases follow [semver](https://semver.org). Pin to a tag if you need stability:

```bash
git clone --branch v0.1.0 https://github.com/kommodoai/kommodo-skill.git
```

Breaking changes get a major bump. Additive changes (new helpers, new optional params) are minor. Bug fixes are patch.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `KOMMODO_API_TOKEN not set` | env var missing | `export` it + restart your shell and Claude Code |
| `401 Unauthorized` | token invalid, revoked, or for another environment | Regenerate at `/account?tab=api`. Verify `KOMMODO_API_BASE_URL` matches the token's environment. |
| `403 Write scope required` | Read-scoped token used for a write endpoint | Regenerate with the **Read + write** toggle |
| `429 Too Many Requests` | Current request pacing limit reached | Honor `Retry-After` and `X-RateLimit-Reset`; do not hard-code a quota |
| `403 Premium subscription required` | API access is unavailable for this account | Confirm the account has active Stripe-backed paid access |
| Skill not detected | Claude Code did not watch a newly created skills directory | Run `/reload-skills`; restart only if it still does not appear |

---

## Contributing

Issues and PRs welcome at https://github.com/kommodoai/kommodo-skill/issues.

This repo is the source of truth for the skill artifact. The underlying API lives in the private dashboard repo; changes to the API surface land here when the API ships a corresponding release.

---

## License

MIT — see [LICENSE](LICENSE).
