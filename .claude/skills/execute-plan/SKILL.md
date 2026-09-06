---
name: execute-plan
description: Execute a markdown plan from docs/plans/ end to end — route each phase to the right specialist agent, implement TDD-first via the red/green/refactor chain, and validate against the plan's own success criteria without substituting or relaxing them. Use when the user says "execute the plan", "/execute-plan <path>", or hands over a plan file. The plan path is the argument.
---

# Execute Plan

Adapted from oh-my-tradeagent. Its Discord notifications, PR-bot loop,
`_workspace/` paths, GitHub-issue coupling and Java routing are **deliberately
absent** — this repo has none of them, and carrying dead references over would
be worse than not having the skill.

## Argument

`$ARG_PATH` — a plan file, normally `docs/plans/PLAN-<date>-<slug>.md`. If no
path is given, list `docs/plans/` and ask which.

## Before starting

Read `docs/spikes/overall.md`. A plan that contradicts a recorded decision
(`D-nn`) or assumes an open question (`Q-nn`) is settled must be halted, not
executed around.

## Workflow

1. **Read the plan.** Extract phases, dependencies, success criteria and halt
   conditions **verbatim**. If success criteria are missing or unenforceable as
   written, say so and stop — do not invent them. An unfalsifiable plan is not
   executable.

2. **Route each phase.** Pick from what this repo actually has:

   | Phase content | Consult | Implement |
   |---|---|---|
   | Behaviour change in a function or module | — | `tdd-red` → `tdd-green` → `tdd-refactor` |
   | Signals, backtesting, risk metrics, market microstructure | `quant-analyst` | review only |
   | LangGraph wiring, state shape, node topology | `mattpocock-skills:codebase-design` | TDD chain |
   | Unknown root cause, code archaeology | `Explore` or `general-purpose` | n/a |
   | Research against external sources | `mattpocock-skills:research` | n/a |
   | Pure config, docs, one-shot scripts, infra wiring | — | direct edit, no TDD |

   Never pick an agent the plan does not imply. Two or three perspectives on a
   phase that genuinely spans domains; one otherwise.

3. **Implement TDD-first.** This is not negotiable in this repo (CLAUDE.md).
   Any phase producing behaviour change runs `tdd-red` → `tdd-green` →
   `tdd-refactor` in order. The red phase must show a real failure before green
   begins. Config/doc/script phases skip the chain — state which applies.

   **Every test must be falsifiable, and you must say how.** For each test
   written, state in one line the change that would make it fail. A test with no
   answer to that is not a test — it is a comment that runs. Tests that assert
   a function returns *something*, that re-implement the implementation, or that
   would still pass with the feature deleted, get rejected in review, not
   merged.

4. **Validate against the plan's own criteria.** Run the plan's stated
   completion criterion for each phase, plus `uv run ruff check` and
   `uv run pytest`. **No substitution and no relaxation**: if the plan said a
   number lands in a range, check that number. "Tests pass" does not discharge a
   criterion the plan wrote differently.

5. **Log what the plan decided.** If execution settled a technical decision,
   reversed one, or surfaced a new unknown, record it with the `spike-log`
   skill. A plan that changed direction and left no trail defeats the log.

6. **Report.** Per phase: what was built, the criterion, and the observed result.
   State plainly what was not done and why.

## Evidence — a claim without a receipt is not a result

The failure this exists to prevent: reporting "tests pass" or "criterion met"
without having run the thing. It is the same error as a plan asserting a row
count nobody executed — plausible, confident, and wrong.

**A phase is not complete until its criterion has been executed and its output
shown.** Not paraphrased, not summarised — the actual output.

Every completed phase reports a verification block:

```
PHASE-n verification
  criterion : <the plan's completion criterion, quoted verbatim>
  command   : <the exact command a reader can re-run>
  output    : <verbatim, trimmed to the decisive lines — never rewritten>
  verdict   : MET / NOT MET
```

Rules:

- **RED evidence is the assertion error**, pasted. A red phase reported without
  the failure text did not happen as far as this skill is concerned.
- **GREEN evidence is the full suite**, not just the new tests. A phase that
  greens its own test while reddening another is not green.
- **The criterion is evaluated on its own terms.** `uv run pytest` passing does
  not discharge a criterion that named a row count, a verdict distribution, or a
  specific command. Run what the plan actually wrote, separately.
- **State the negative control.** One line per phase: what would have made this
  fail? If nothing would, the criterion is decorative and that is a finding.
- **Never relay evidence you did not observe.** If a sub-agent claims a pass
  without output, ask for the output or re-run it yourself before reporting it.
  An agent's assertion is a claim, not a receipt.
- **Trim, never edit.** Cutting 200 passing lines to the summary line is fine.
  Rewording an error, rounding a number, or omitting a failure is not.

At the end of the run, the report carries one verification block per phase.
Anything without one is reported as **not verified**, regardless of whether it
looks finished.

## When evidence is impossible, the design is wrong

Reporting "not verified" and moving on normalises the thing this skill exists to
prevent. **Unverifiability is a defect in the implementation, not a status of
the phase.** If you cannot produce evidence, stop and change the design until
you can.

When a criterion cannot be executed or a behaviour cannot be observed, do **not**
substitute a weaker proxy check and do not accept it as an inherent limit.
Diagnose it first — it is almost always one of:

| Symptom | Underlying defect | The design change |
|---|---|---|
| The behaviour has no observable output | No seam — it is buried in a closure, a private path, or mutated state | Extract the seam; return a value instead of mutating |
| It only shows up as a side effect you cannot trigger | Un-injected dependency: network, clock, randomness, filesystem | Inject it, so a test can force the branch |
| Output varies between identical runs | Real nondeterminism, or an unstable ordering | Make ordering explicit; seed or inject the source |
| Some inputs vanish without trace | They exit early on a path that emits nothing | Emit an explicit outcome for them, so absence becomes a value |
| The criterion names a quality, not a behaviour | It was never checkable | Rewrite it as an observable, or drop it |

Then change the implementation so the behaviour becomes observable, and only
then run the phase. The change is in scope: making a phase verifiable is part of
building it, not a separate concern.

If the design genuinely cannot be made observable, that is a finding that
reopens the plan — record it with `spike-log` and stop. It is never a phase that
passes on the strength of looking finished.

## Halt conditions

Stop and report rather than working around:

- A phase contradicts a recorded decision, or depends on an open question.
- Success criteria are absent, ambiguous, or unverifiable.
- Three consecutive failed attempts at one phase.
- The change would need to grow beyond the scope locked in step 2.

## Reporting — non-negotiable

Sub-agents run silently by default and that is the wrong default here. The user
must never have to ask what is happening.

- **Announce every dispatch before it runs**, in one line each: which agent,
  which phase, and why that agent. Never dispatch and go quiet.
- **On the first dispatch of a run**, say that `/agents` shows the live view and
  `/tasks` lists background work. Say it once, not every time.
- **Relay at every phase boundary**: what the agent did, what the criterion was,
  whether it held. A phase that finishes without a report has not finished.
- **If a dispatch runs long with nothing to relay, say that too.** "Still
  reading the graph code, no output yet" is information; silence is not.
- **Never summarise or predict a result you have not received.** If an agent has
  not reported, say it has not reported.

## Discipline

- **Do not relax the plan to make it pass.** If the criterion cannot be met, that
  is the finding.
- **Do not expand scope silently.** Adjacent improvements are a separate plan.
- **Do not skip red.** A test written after the implementation is a regression
  test, not a specification, and the difference is the whole point.
