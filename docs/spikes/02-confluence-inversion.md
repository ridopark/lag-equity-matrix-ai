# 02 — Inverting the pipeline: graph as confluence/evidence for a chosen equity

**Question:** Instead of predicting laggers from a leader shock, can the same
graph be run backward — you pick an equity, the system gathers supporting
evidence, and more evidence means more confidence?
**Date:** 2026-09-03
**Status:** done (analysis, no code, no data)

## What we tried

Reasoned about the proposal against three bodies of work: the lead-lag
literature already surveyed in spike 01, the statistics of correlated evidence,
and the behavioural literature on confirmation bias in investment research. No
implementation, no backtest.

## What we measured

### The instinct is sound: this is the more defensible use of the graph

Spike 01 established that the lead-lag anomaly is real but heavily arbitraged
(Cohen & Frazzini published 2008). A prediction engine lives or dies on residual
alpha. A **decision-support** tool does not — exposure mapping and evidence
assembly are useful even if the anomaly is fully priced. Inverting the pipeline
removes the project's dependence on its weakest assumption.

### But "more evidence = more confidence" is backwards for this graph

The proposal's stated mechanism fails on the statistics, and it fails *hardest*
in exactly the case the graph is built for.

If you pick NVDA and gather: TSM up, AVGO up, AMD up, ASML up, SOXX up — that is
not five pieces of evidence. It is approximately **one** piece of evidence
(semiconductors are up) counted five times. The neighbours are correlated by
construction; the graph selected them *because* they are economically linked.

This is the conditional-independence failure of naive Bayes, and its known
symptom is precisely overconfidence: "whenever the conditional independence
assumption is violated… the probability estimates tend to be more extreme
(closer to zero or one) than they should otherwise be", and "when two pieces of
evidence are correlated, updating on each of them may overestimate the strength
of the evidence."

The perverse consequence: **the more tightly linked the neighbourhood, the more
confident a naive confluence score becomes, and the less independent information
it actually contains.** A dense cluster in the graph is a *warning* that the
evidence is redundant. The graph is a map of the dependence structure — it should
be used to *discount* correlated evidence, not to accumulate it.

### The direction of the edge decides whether the evidence is predictive at all

This is the sharpest constraint and it is not symmetric:

| You picked | Neighbour evidence | Valid? |
|---|---|---|
| A **lagger** (leader already moved, it hasn't) | Leader's recent move | **Yes** — this is Cohen & Frazzini entered from the other end. Genuinely predictive. |
| A **leader** (it moved, neighbours haven't) | Laggers' recent moves | **No** — backward-looking. Tells you what already happened, not what comes next. |
| Either | Neighbour moved *at the same time* | **No** — contemporaneous co-movement is a beta measurement, not evidence about the future. |

A confluence score that does not distinguish these three cases will silently mix
one genuine signal with two that carry no predictive content.

### Pick-then-justify is a rationalisation engine

"When an equity is picked" leaves the picking outside the system. Building a tool
whose job is to assemble support for an already-made choice reproduces the exact
failure the behavioural literature documents: investors are "significantly more
likely to read articles supporting their decision rather than those opposing
it", and remain "unwilling to consider or accept evidence that they are wrong."

Any sufficiently connected node has neighbours doing well *and* neighbours doing
badly. Without a pre-committed rule for what counts as disconfirming, the system
will always find a case — and its confidence number will mean nothing.

The structural countermeasure is well established: pre-mortems and red-team
exercises exist to build the search for disconfirming evidence into the process,
and prospective hindsight improves identification of failure reasons by ~30%.

## Conclusion

Keep the inversion; replace the scoring mechanism. Four framings survive
scrutiny, roughly in order of defensibility:

1. **Lagger-entry screening (strongest).** Do not ask "find support for NVDA."
   Ask "which of my candidates is a lagger whose leader has already moved and
   which has not yet repriced?" Same anomaly, same graph, entered from the other
   end — and it stays predictive rather than confirmatory.

2. **Independence-weighted evidence, not evidence counting.** Report an
   *effective* number of independent signals, not a raw count. Meucci's Effective
   Number of Bets — an entropy measure over uncorrelated principal components,
   later refined to Minimum-Torsion Bets — is the established formulation, and it
   maps directly: cluster the neighbourhood, weight by independence, and surface
   "7 corroborating names, effective independent evidence ≈ 1.4." That number is
   the honest one, and it is a far better portfolio-project artefact than a
   confluence score.

3. **Falsification over confirmation.** Make the primary output the strongest
   *case against*: which linked names are moving the wrong way, which of the
   thesis is already priced in by the neighbourhood, what would have to be true
   for this to fail. A graph-driven red-team is more useful and more defensible
   than a graph-driven cheerleader.

4. **Exposure decomposition (no alpha claim needed).** "If I buy NVDA, what am I
   actually exposed to?" — CoWoS packaging, TSMC concentration, hyperscaler
   capex. Plus a crowding check: if the portfolio already holds four
   semiconductor names, the graph shows the fifth adds almost nothing. This
   requires no anomaly to still be tradeable.

The honest headline: **the inversion improves the project; the proposed scoring
rule would quietly make it worse.** Confluence over a correlated neighbourhood
manufactures confidence rather than measuring it, and it does so most
aggressively when the graph is working best.
