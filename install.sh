#!/usr/bin/env bash
# One-shot installer for the Kommodo skill.
#
# Default: installs to the personal scope (~/.claude/skills/kommodo) as a
# symlink back to this directory, so `git pull` upgrades the skill in place.
#
# Pass --copy to copy files instead of symlinking (useful when the source
# tree might be moved or deleted).
#
# Pass --project <path> to install into <path>/.claude/skills/kommodo instead
# of the personal scope.
#
# Pass --force to replace an existing installation. Without it, the installer
# refuses to remove an existing skill directory.

set -euo pipefail

MODE="symlink"
PROJECT_DIR=""
FORCE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --copy) MODE="copy"; shift ;;
    --project)
      if [[ $# -lt 2 ]]; then
        echo "--project requires a path" >&2
        exit 1
      fi
      PROJECT_DIR="$2"
      shift 2
      ;;
    --force) FORCE="true"; shift ;;
    -h|--help)
      awk '/^[^#!]/{exit}/^# /{sub(/^# /,"");print}' "$0"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

SKILL_SRC="$(cd "$(dirname "$0")" && pwd)"
SKILL_NAME="$(awk -F': *' '/^name:/{print $2; exit}' "$SKILL_SRC/SKILL.md")"
SKILL_NAME="${SKILL_NAME:-kommodo}"

if [[ -n "$PROJECT_DIR" ]]; then
  TARGET_ROOT="$PROJECT_DIR/.claude/skills"
else
  TARGET_ROOT="$HOME/.claude/skills"
fi
TARGET="$TARGET_ROOT/$SKILL_NAME"

mkdir -p "$TARGET_ROOT"

if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  if [[ "$FORCE" != "true" ]]; then
    echo "$TARGET already exists; re-run with --force to replace it" >&2
    exit 1
  fi
  echo "Replacing existing $TARGET"
  rm -rf "$TARGET"
fi

if [[ "$MODE" == "symlink" ]]; then
  ln -s "$SKILL_SRC" "$TARGET"
  echo "Linked $TARGET -> $SKILL_SRC"
else
  cp -R "$SKILL_SRC" "$TARGET"
  rm -rf "$TARGET/.git"
  echo "Copied $SKILL_SRC -> $TARGET"
fi

if [[ -z "${KOMMODO_API_TOKEN:-}" ]]; then
  cat <<EOF

Skill installed. One more step — set your API token:

  printf 'Paste your Kommodo API token: '
  IFS= read -r -s KOMMODO_API_TOKEN
  export KOMMODO_API_TOKEN
  printf '\n'

Then start a new Claude Code session so it inherits the token. If /kommodo is
not listed, run /reload-skills.
EOF
else
  echo
  echo "Skill installed. KOMMODO_API_TOKEN is set — you're ready. Run /reload-skills if /kommodo is not listed."
fi
