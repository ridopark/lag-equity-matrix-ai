#!/usr/bin/env bash
# PreCompact hook: re-inject the decision log so it survives compaction.
# Silent no-op if the log is missing or jq is unavailable.
set -uo pipefail

LOG="${CLAUDE_PROJECT_DIR:-.}/docs/spikes/overall.md"
CAP=60000

[ -f "$LOG" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

size=$(wc -c < "$LOG")
{
  head -c "$CAP" "$LOG"
  if [ "$size" -gt "$CAP" ]; then
    printf '\n\n[log truncated at %s of %s bytes — read docs/spikes/overall.md for the rest]\n' "$CAP" "$size"
  fi
} | jq -Rs '{
  hookSpecificOutput: {
    hookEventName: "PreCompact",
    additionalContext: ("Project decision log (docs/spikes/overall.md). Carry these decisions, their rationale, and the open questions through compaction — do not re-litigate a decision recorded here, and do not drop an open question. Record new ones with the spike-log skill.\n\n" + .)
  },
  suppressOutput: true
}'
