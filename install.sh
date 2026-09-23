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

set -euo pipefail

MODE="symlink"
PROJECT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --copy) MODE="copy"; shift ;;
    --project) PROJECT_DIR="$2"; shift 2 ;;
    -h|--help)
      awk '/^[^#!]/{exit}/^# /{sub(/^# /,"");print}' "$0"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

SKILL_SRC="$(cd "$(dirname "$0")" && pwd)"
# Name the install after the skill's frontmatter `name:` (not the clone's
# directory name, which is usually `kommodo-skill`).
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
  echo "Removing existing $TARGET"
  rm -rf "$TARGET"
fi

if [[ "$MODE" == "symlink" ]]; then
  ln -s "$SKILL_SRC" "$TARGET"
  echo "Linked $TARGET -> $SKILL_SRC"
else
  cp -R "$SKILL_SRC" "$TARGET"
  echo "Copied $SKILL_SRC -> $TARGET"
fi

if [[ -z "${KOMMODO_API_TOKEN:-}" ]]; then
  cat <<EOF

Skill installed. One more step — set your API token in ~/.zshenv (not
~/.zshrc: agent shells are non-interactive and never read ~/.zshrc):

  echo 'export KOMMODO_API_TOKEN="<paste from https://kommodo.ai/account?tab=api>"' >> ~/.zshenv

Then fully quit and relaunch Claude Code.
EOF
else
  echo
  echo "Skill installed. KOMMODO_API_TOKEN is set — you're ready. Relaunch Claude Code to pick up the skill."
fi
