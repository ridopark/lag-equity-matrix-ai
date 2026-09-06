---
name: spike-log
description: Maintain the research and decision log in docs/spikes/. Use whenever a technical decision is made, changed, or reversed (library/database/model/schema/protocol choice, an architectural change, a rejected alternative); whenever an unknown, risk, or unanswered question is surfaced that will need investigation; whenever a spike, experiment, benchmark, or piece of research concludes; and before changing architectural direction, to check what was already decided and why. Also use when the user says "log this", "record this decision", "write this up as a spike", "what did we decide about X", or "what's still open". Takes optional arguments — `latest` returns a short prose summary of where the project currently stands, and `open` lists the decisions not yet made or not yet finalised, ordered by what they block. Both are read-only.
---

# Spike & Decision Log

`docs/spikes/overall.md` is the project's memory of *why*. Code shows what was
built; this shows what was considered and rejected, and what is still unknown.

## Before anything else

Read `docs/spikes/overall.md`. It holds three tables — Decision Log (`D-nn`),
Open Questions (`Q-nn`), and Spike Index. Never guess the next ID; read the
highest one present and increment. Never renumber or delete an existing row.

If the file does not exist, create it from the section templates below rather
than inventing a new format.

## Arguments

`latest` — run operation 4 (summarise) and nothing else. Read-only.

`open` — run operation 5 (what is undecided) and nothing else. Read-only.

No argument — infer which of operations 1-3 the situation calls for.

## The five operations

### 1. Log a decision

Append an entry to the Decision Log. Five fields, always, in this order:

```markdown
### D-nn — <one-line summary: what was decided, as a noun phrase>
- **When:** <full ISO timestamp — get it from `date -Iseconds`, never guess>
- **Decision:** <what we are doing, concretely enough to act on>
- **Why:** <why this over the alternative — name the alternative>
- **Outcome:** <what actually happened once we lived with it; `pending` until observed>
- **Status:** Accepted | Tentative — settled by Q-nn | Superseded by D-mm
```

Rules:
- **The heading is the summary.** One line, scannable in a list of forty entries.
- **Timestamp comes from `date -Iseconds`.** Run it. Do not reconstruct a time
  from memory and do not write a date when a timestamp is available.
- **Why names the alternative that lost.** "Qdrant for the vector index" is not a
  why; "Qdrant over pgvector — the query is similarity *plus* `symbol IN (...)`
  *plus* recency, and pgvector's filtered-search cost at our latency budget is
  unmeasured" is.
- **Outcome is the field that closes the loop.** It starts as `pending`. Come
  back and fill it in when the decision has been exercised — "Working, verified
  by X", "Failed: Y", "Never exercised". A log that only records intent tells you
  what you meant to do, never whether you were right. **When any work proves or
  disproves an earlier decision, update that entry's Outcome** — this is as
  important as writing new entries.
- **`Tentative` is for unvalidated guesses** — a default picked to keep moving,
  not yet measured. A tentative decision must point at the Open Question that
  would settle it.
- **Reversals supersede, they do not overwrite.** Add a *new* entry with the new
  decision, then edit the old entry's Status to `Superseded by D-nn` and its
  Outcome to say what went wrong. The old reasoning stays readable — that is the
  whole point of the log.

### 2. Log an open question

Add a row to Open Questions:

```
| Q-nn | <the question, ending in ?> | <the file, module, or D-nn it blocks> | <what would answer it> |
```

Only log questions whose answer would *change what we build*. "Should we add
type hints" is not an open question; "is an LLM affordable in the hot path, or
does the fast signal need to be pure arithmetic" is.

When a question is answered, do not delete the row — strike it and add the
answering decision:

```
| ~~Q-nn~~ | ... | ... | Answered by D-mm |
```

### 3. Write up a spike

For actual investigation — a benchmark, a prototype, a read of someone else's
implementation — create `docs/spikes/NN-<short-slug>.md` (zero-padded, next
number in sequence):

```markdown
# NN — <Title>

**Question:** <the Q-nn this answers, restated in one line>
**Date:** YYYY-MM-DD
**Status:** in progress | done | abandoned

## What we tried
<approach, and what was deliberately not tried>

## What we measured
<numbers, errors, output. Raw enough that someone can disagree with the conclusion.>

## Conclusion
<what this means for the project — and the honest limits of the result>
```

Then add it to the Spike Index in `overall.md`, and log any decision it produced
(operation 1) and mark the question answered (operation 2). A spike that changed
nothing still gets indexed — a measured dead end is worth as much as a win.

### 4. Summarise the current state (`latest`)

Invoked as `/spike-log latest`. **Read-only — this operation never writes to the
log.** If something needs recording, that is operation 1, 2 or 3; say so and stop.

Run `python3 .claude/skills/spike-log/audit.py` first (described under operation
5), then read `docs/spikes/overall.md` and skim the most recent spike files.
Produce **three to five short paragraphs of prose.** Not a report, not tables,
not bullet lists, not an enumeration of every ID.

**Say nothing about the audit when it passes.** "Integrity check clean" in a
five-paragraph summary is noise, and a reader who has to be told the log is
consistent will stop trusting that it usually is. Surface it only when it fails.

Cover, in this order:

1. **What is blocking.** Lead here, not with chronology. Name the open questions
   that currently gate work and say what they gate.
2. **Where the project stands** — the shape of the thing as currently decided,
   in a sentence or two. What it is, and what it deliberately is not.
3. **What changed most recently** — the last few decisions, and anything they
   superseded or closed.
4. **The honesty check.** Count Decision entries whose Outcome is still
   `pending`. A log full of pending outcomes means decisions were made and never
   checked against reality — report the ratio plainly, because it is the single
   best indicator that the log has drifted from being useful. **If the audit
   found problems, they belong in this paragraph**, in a sentence of prose, not
   as a dump of the script's output: an orphaned gate or a dangling reference is
   the same failure as a stale `pending` outcome, only sharper — the log has
   stopped describing the project. Name what is wrong and say it needs operation
   1; do not fix it here.
5. **What to do next**, in one sentence.

Rules:

- **Prose that reads aloud.** Someone returning after two weeks should absorb it
  in under a minute. If it needs a table, it is too long.
- **Cite IDs inline** (`D-16`, `Q-18`) so the reader can jump to detail, but do
  not list them out — the point is the story, not the index.
- **Do not soften.** If the project's central premise is untested, that belongs
  in the first paragraph, not the last.
- Superseded decisions are part of the story when they explain a reversal;
  otherwise leave them out.

### 5. What is still undecided (`open`)

Invoked as `/spike-log open`. **Read-only.** Answers one question: what do we
still owe a decision on, and what is it holding up?

**First, always, run the integrity check:**

```bash
python3 .claude/skills/spike-log/audit.py
```

It exits 0 clean, 1 with problems, 2 if the log is missing, and catches the four
ways this log rots silently: references to `D-nn`/`Q-nn` that no longer exist,
Tentative decisions whose gating question has since been closed, duplicate IDs,
and a Spike Index that has drifted from the files on disk.

Report any problems **before** the worklist, because they make the worklist
wrong — a Tentative decision gated on a closed question reads as open work when
it is really an entry nobody finalised. Do not fix them silently in the middle
of a read-only operation: name them, and offer operation 1.

An orphaned gate is not a formatting nit. It means a decision stopped being
provisional and nobody noticed, so the guess is still in force and still
labelled unconfirmed.

Two distinct populations, and they must not be merged:

- **Not made** — Open Questions. No decision exists yet.
- **Not finalised** — Decision entries with `Status: Tentative`. A decision
  exists but was a guess taken to keep moving, and each one names the question
  that would settle it.

**Do not include entries whose *Outcome* is `pending`.** That is a third and
different thing — a decision that was made and finalised but never checked
against reality. It belongs in operation 4's honesty check, not here. Conflating
them turns a short worklist into a list of everything.

Output a worklist, not prose — this operation is a queue, so a table or grouped
list is correct here even though operation 4 forbids them. Order by **blast
radius**: how much other work is waiting on it, not by ID and not by age.

Group as:

1. **Blocking** — answering this unblocks other work. Say what it unblocks.
2. **Tentative decisions** — the guess currently in force, and the question that
   would confirm or overturn it. Name what breaks if the guess is wrong.
3. **Open, not blocking** — visible but parked, one line each.

For every item give **what would actually answer it** — a measurement, a
document, a decision from the user. An item nobody knows how to close is not a
question, it is a wish; say so.

Close with the single cheapest item to resolve. Momentum on a decision log comes
from closing things, and the cheapest one is usually not the most important one.

## Discipline

- **Append, don't rewrite.** The value is in the trail, including the wrong turns.
- **One line per row.** If a rationale needs a paragraph, it needs a spike file;
  link to it.
- **Record the decision when it is made**, while the alternatives are still fresh
  — not reconstructed later from the code.
- **Do not pad the log.** A row for every routine choice makes the real decisions
  invisible. Log what a new engineer would otherwise ask "why is it like this?"
  about.
- Prefer editing the tables in place with a targeted edit; do not regenerate the
  whole file.

## Section templates

If `overall.md` must be created:

```markdown
# Spikes & Decisions — Running Log

Research notes, spike results, and the decisions that came out of them. Append as
we go; don't rewrite history — supersede an entry instead.

## Decision Log

### D-01 — <summary>
- **When:** <ISO timestamp>
- **Decision:** ...
- **Why:** ...
- **Outcome:** pending
- **Status:** Accepted

## Open Questions

| ID | Question | Blocks | Notes |
|----|----------|--------|-------|

## Spike Index

_None yet._
```
