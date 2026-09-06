#!/usr/bin/env bash
# PostToolUse(Bash) hook, gated to `git commit`: prompt a decision-log entry.
set -uo pipefail

cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"A commit just landed. If it encodes a technical decision, reverses one, or surfaced a new unknown, append an entry to docs/spikes/overall.md using the spike-log skill, in the standard shape: a one-line summary heading, then When (full ISO timestamp from `date -Iseconds`), Decision, Why (naming the alternative that lost), Outcome (`pending` until observed), and Status. Also update the Outcome field of any earlier entry this commit just proved or disproved. If the commit is routine, do nothing and do not mention this reminder."},"suppressOutput":true}
JSON
