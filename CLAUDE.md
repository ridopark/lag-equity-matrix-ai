# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## Project-specific (lag-equity-matrix-ai)

### Plan before implementing

Anything beyond a one-file change starts with a plan. Use the
`implementation-plan` agent to write it to `docs/plans/`, then `/execute-plan`
to run it. Plans cite the decisions (`D-nn`) they depend on and halt rather than
work around a contradiction.

### TDD is mandatory

Behaviour changes go red → green → refactor, in that order, via the `tdd-red`,
`tdd-green` and `tdd-refactor` agents:

- The failing test is written **first** and its failure is **observed** before
  any implementation exists.
- `tdd-green` writes the minimum to pass and never edits a test.
- `tdd-refactor` changes design, never behaviour.

Exempt: pure configuration, documentation, one-shot scripts under `scripts/`,
and infrastructure wiring with no behavioural change. Say which exemption
applies rather than skipping tests silently.

### Decisions get logged

Technical decisions, reversals and newly surfaced unknowns go in
`docs/spikes/overall.md` via the `spike-log` skill. Run `/spike-log open` before
changing direction.

### Subagents report, always

Any dispatch to a subagent is announced before it runs — which agent, for what,
and why that one — and its result is relayed when it lands. Silence while an
agent works is a defect, not a style choice: if there is nothing to report yet,
report that. Never state or summarise a result that has not actually come back.

`/agents` shows the live subagent view; `/tasks` lists background work.

### Unverifiable means badly designed

If a behaviour cannot be observed, that is a defect in the implementation, not a
property of the problem. Do not substitute a weaker check and do not report it
as an accepted limitation — find the missing seam, inject the dependency, make
the ordering explicit, or give the silent path an explicit outcome, and then
verify it. Redesigning for observability is part of building the thing.
