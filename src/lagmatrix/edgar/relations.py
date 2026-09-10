"""Re-classify `filing_mention` rows from the stored `passage` text.

The audit found only 69% of stored rows are genuine customer disclosures; the
rest are competitor lists, acquisition announcements, reversed relationships
(the counterparty supplies the filer rather than buying from it), and mentions
that name a party without stating any relation. Both functions here read off
the *one sentence* that actually names the counterparty, never the whole
passage -- a passage-wide search is what let a window-opening aggregate
percentage ("our ten largest customers accounted for 69%...") get attached to
a specific counterparty's mention ("Apple Inc. accounted for 27.7%...")
several sentences later.
"""

from __future__ import annotations

import re

# Corporate abbreviations whose trailing period must not be treated as a
# sentence boundary.
_ABBREVIATIONS = ("Inc.", "Corp.", "Ltd.", "Co.", "U.S.")

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")

_PCT = re.compile(r"(\d+(?:\.\d+)?)%")

_COMPETITOR = ("compet",)
_CORPORATE_ACTION = ("acqui", "sale of")
_REVERSED = ("supplier", "supplied by")
_CUSTOMER = ("customer", "accounted for", "sales to", "represented")


def _split_sentences(passage: str) -> list[str]:
    protected = passage
    for abbr in _ABBREVIATIONS:
        protected = protected.replace(abbr, abbr.replace(".", "\0"))
    sentences = _SENTENCE_BREAK.split(protected)
    return [s.replace("\0", ".") for s in sentences]


def naming_sentence(passage: str, counterparty: str) -> str | None:
    """The one sentence in `passage` that names `counterparty`, or None.

    Matches on the counterparty's leading token (e.g. "Apple" out of "Apple
    Inc.") since filing prose rarely repeats the full legal name, including
    its corporate designator, at the point it names the party.
    """
    if not passage or not counterparty:
        return None
    token = counterparty.split()[0]
    pattern = re.compile(r"\b" + re.escape(token) + r"\b")
    for sentence in _split_sentences(passage):
        if pattern.search(sentence):
            return sentence
    return None


def classify(passage: str, counterparty: str) -> tuple[str, str | None, str | None]:
    """Relation label, naming sentence, and percentage -- all from one sentence.

    Precedence when a sentence matches more than one cue:
    competitor > corporate_action > reversed > customer > unstated.
    """
    sentence = naming_sentence(passage, counterparty)
    if sentence is None:
        return "unnamed", None, None

    lowered = sentence.lower()
    if any(cue in lowered for cue in _COMPETITOR):
        relation = "competitor"
    elif any(cue in lowered for cue in _CORPORATE_ACTION):
        relation = "corporate_action"
    elif any(cue in lowered for cue in _REVERSED):
        relation = "reversed"
    elif any(cue in lowered for cue in _CUSTOMER):
        relation = "customer"
    else:
        relation = "unstated"

    match = _PCT.search(sentence)
    pct = match.group(1) if match else None
    return relation, sentence, pct
