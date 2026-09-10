"""PHASE spec for `lagmatrix.edgar.relations`: re-classifying `filing_mention`
rows from the stored `passage` text, without re-crawling EDGAR.

The audit found only 69% of the 1,211 stored rows are genuine customer
disclosures; the rest are competitor lists, acquisition announcements,
reversed relationships (the counterparty supplies the FILER), and mentions
that name a party without stating any relation. `relation()` on the old
extractor collapses all of these into `"customer"` or `"unknown"`. This module
replaces that with six explicit labels, read off the one sentence in the
passage that actually names the counterparty -- never the whole +/-420-char
window, which is what let Amkor's window-opening "69%" (about the ten largest
customers in aggregate) get attached to the Apple-specific mention instead of
the "27.7%" that actually sits next to Apple's name.

Precedence when a sentence matches more than one cue (D: competitor talk about
a named party outranks incidental "customers" language in the same sentence):
    competitor > corporate_action > reversed > customer > unstated

All passages below are written in the style of the audited filings; none are
placeholders.
"""

from __future__ import annotations

import pytest

from lagmatrix.edgar.relations import classify, naming_sentence

# ---------------------------------------------------------------------------
# naming_sentence: sentence extraction and the "unnamed" edge cases
# ---------------------------------------------------------------------------


def test_naming_sentence_none_when_counterparty_never_appears():
    """No sentence in the passage names the counterparty -> None, not an
    empty string or the whole passage. Would fail if the function fell back
    to returning the passage when it could not find a match."""
    passage = ("Our net sales increased 8% year over year, driven primarily "
               "by strength in the automotive and industrial end markets.")
    assert naming_sentence(passage, "Apple Inc.") is None


def test_naming_sentence_none_for_empty_passage():
    """An empty passage has no sentence to return. Would fail if the
    function skipped the empty-input guard and raised or returned ""."""
    assert naming_sentence("", "Apple Inc.") is None


def test_naming_sentence_none_for_empty_counterparty():
    """An empty counterparty names nothing. Would fail if the function
    treated "" as a wildcard that matches every sentence."""
    passage = "Direct sales to Apple Inc. accounted for 27.7% of our net sales."
    assert naming_sentence(passage, "") is None


def test_naming_sentence_matches_leading_token_when_designator_absent():
    """counterparty="Apple Inc." must match text that only says "Apple" --
    the corporate designator is not required in the prose. Would fail if the
    function required the full counterparty string as a literal substring."""
    passage = ("Our largest end customer, Apple, represented approximately "
               "18% of net revenue in the current fiscal year.")
    assert naming_sentence(passage, "Apple Inc.") == passage


def test_naming_sentence_matches_leading_token_of_a_longer_legal_name():
    """The reverse direction: counterparty is the fuller legal name
    ("GLOBALFOUNDRIES U.S. Inc.") but the filing prose only ever says the
    short public name. Would fail if matching required the counterparty
    string to appear verbatim in the sentence rather than just its leading
    token."""
    passage = ("Substantially all of our wafers are primarily supplied by "
               "GLOBALFOUNDRIES under a long-term wafer supply agreement "
               "that expires in 2028.")
    assert naming_sentence(passage, "GLOBALFOUNDRIES U.S. Inc.") == passage


@pytest.mark.parametrize("passage, counterparty, expected_sentence", [
    pytest.param(
        "We reported strong results this quarter. Apple Inc. accounted for "
        "27.7% of our net sales this year. Other customers were less "
        "significant.",
        "Apple Inc.",
        "Apple Inc. accounted for 27.7% of our net sales this year.",
        id="Inc.",
    ),
    pytest.param(
        "Market conditions remained volatile in the quarter. Sales to "
        "Micron Technology Corp. accounted for 15% of our net revenue "
        "during the period. We continue to diversify our customer base.",
        "Micron Technology Corp.",
        "Sales to Micron Technology Corp. accounted for 15% of our net "
        "revenue during the period.",
        id="Corp.",
    ),
    pytest.param(
        "Demand from Asia remained stable this year. Our key customers "
        "include Samsung Electronics Co., Ltd. and other large OEMs. We "
        "expect this concentration to continue.",
        "Samsung Electronics Co., Ltd.",
        "Our key customers include Samsung Electronics Co., Ltd. and "
        "other large OEMs.",
        id="Co.-and-Ltd.-together",
    ),
    pytest.param(
        "Backlog increased slightly in the fourth quarter. Sales to "
        "Skyworks Solutions Co. accounted for 9% of our net sales in "
        "fiscal 2023. Margins were flat sequentially.",
        "Skyworks Solutions Co.",
        "Sales to Skyworks Solutions Co. accounted for 9% of our net "
        "sales in fiscal 2023.",
        id="Co.-alone",
    ),
    pytest.param(
        "Demand from Asia and Europe remained stable in the period. Our "
        "U.S. operations rely on wafers supplied by GLOBALFOUNDRIES under "
        "a long-term agreement expiring in 2028. We do not expect "
        "near-term supply constraints.",
        "GLOBALFOUNDRIES",
        "Our U.S. operations rely on wafers supplied by GLOBALFOUNDRIES "
        "under a long-term agreement expiring in 2028.",
        id="U.S.",
    ),
])
def test_naming_sentence_does_not_split_on_corporate_abbreviations(
        passage, counterparty, expected_sentence):
    """A sentence-splitter that breaks on every period would cut these
    sentences in half at "Inc.", "Corp.", "Co.", "Ltd." or "U.S.". Would
    fail (return a truncated fragment, or None) if the split were done on a
    bare `.` instead of one that excludes these abbreviations."""
    assert naming_sentence(passage, counterparty) == expected_sentence


# ---------------------------------------------------------------------------
# classify: one relation label at a time
# ---------------------------------------------------------------------------

CLASSIFY_CASES = [
    pytest.param(
        "Direct sales to Apple Inc. accounted for 27.7% of our net sales "
        "for the year ended December 31, 2023.",
        "Apple Inc.",
        "customer", None, "27.7",
        id="customer-direct-sales-accounted-for",
    ),
    pytest.param(
        "Our key customers include Amazon, Apple Inc., and Samsung "
        "Electronics, and the loss of any one of them would materially "
        "affect our revenue.",
        "Apple Inc.",
        "customer", None, None,
        id="customer-key-customers-include",
    ),
    pytest.param(
        "Apple Inc., one of our end customers, represented approximately "
        "18% of total net revenue in fiscal 2023.",
        "Apple Inc.",
        "customer", None, "18",
        id="customer-end-customer-apposition",
    ),
    pytest.param(
        "We compete against Broadcom Inc. and other large semiconductor "
        "companies for module business, and price pressure has increased "
        "in recent quarters.",
        "Broadcom Inc.",
        "competitor", None, None,
        id="competitor-compete-against",
    ),
    pytest.param(
        "Our competitors include Broadcom, Skyworks Solutions, and Murata "
        "Manufacturing, among others, some of which have greater "
        "financial resources than we do.",
        "Broadcom",
        "competitor", None, None,
        id="competitor-competitors-include",
    ),
    pytest.param(
        "We face intense competition in the RF front-end market, "
        "including from Broadcom, and must continue to serve our largest "
        "customers despite this pressure.",
        "Broadcom",
        "competitor", None, None,
        id="competitor-precedence-over-customer-wording",
    ),
    pytest.param(
        "During the year, we acquired certain assets of Ion Photonics "
        "Inc. for approximately $18 million in cash, expanding our "
        "optical sensing portfolio.",
        "Ion Photonics Inc.",
        "corporate_action", None, None,
        id="corporate-action-we-acquired-certain-assets",
    ),
    pytest.param(
        "On November 22, 2023, VMware was acquired by Broadcom Inc. in "
        "an all-cash and stock transaction valued at approximately $69 "
        "billion, and we are monitoring the impact on our enterprise "
        "networking customers.",
        "VMware",
        "corporate_action", None, None,
        id="corporate-action-was-acquired-by-precedence-over-customers",
    ),
    pytest.param(
        "In August 2022 we completed the sale of certain of our "
        "enterprise security assets to Broadcom Inc. for total "
        "consideration of approximately $200 million.",
        "Broadcom Inc.",
        "corporate_action", None, None,
        id="corporate-action-sale-of-assets-to",
    ),
    pytest.param(
        "Our primary suppliers are Taiwan Semiconductor Manufacturing "
        "Company and GLOBALFOUNDRIES, and we do not have long-term supply "
        "agreements guaranteeing wafer availability with either.",
        "GLOBALFOUNDRIES",
        "reversed", None, None,
        id="reversed-active-primary-suppliers-are",
    ),
    pytest.param(
        "We rely on a few major suppliers: GLOBALFOUNDRIES, TSMC, and "
        "Amkor Technology, for substantially all of our wafer fabrication "
        "and assembly and test services.",
        "GLOBALFOUNDRIES",
        "reversed", None, None,
        id="reversed-active-rely-on-suppliers-list",
    ),
    pytest.param(
        "Substantially all of our wafers are primarily supplied by "
        "GLOBALFOUNDRIES under a long-term wafer supply agreement that "
        "expires in 2028.",
        "GLOBALFOUNDRIES",
        "reversed", None, None,
        id="reversed-passive-wafers-supplied-by",
    ),
    pytest.param(
        "Certain of our finished products are supplied by Amkor "
        "Technology, Inc. under a multi-year assembly and test agreement.",
        "Amkor Technology, Inc.",
        "reversed", None, None,
        id="reversed-passive-products-supplied-by",
    ),
    pytest.param(
        "Broadcom was mentioned prominently in a Wall Street Journal "
        "feature on semiconductor supply chain consolidation trends "
        "published last month.",
        "Broadcom",
        "unstated", None, None,
        id="unstated-named-without-any-relation",
    ),
]


@pytest.mark.parametrize(
    "passage, counterparty, expected_relation, _unused_sentence, expected_pct",
    CLASSIFY_CASES,
)
def test_classify_relation_label(
        passage, counterparty, expected_relation, _unused_sentence, expected_pct):
    """One case per relation label (customer x3, competitor x3 including the
    precedence case, corporate_action x3, reversed x4, unstated x1). Each
    would fail if the label logic mis-ranked its cue against a competing one,
    e.g. reporting "customer" for a sentence whose subject is competition or
    an acquisition, or "unstated" for a sentence that does name a relation."""
    relation, sentence, pct = classify(passage, counterparty)
    assert relation == expected_relation
    assert sentence == passage
    assert pct == expected_pct


def test_classify_unnamed_when_counterparty_never_appears():
    """No sentence in the passage names the counterparty at all -> relation
    is "unnamed" and both sentence and pct are None. Would fail if the
    fallback for "found no naming sentence" were "unstated" instead of the
    distinct "unnamed" label the audit needs to tell apart "named but silent"
    from "never named"."""
    passage = ("Our net sales increased 8% year over year, driven "
               "primarily by strength in the automotive and industrial "
               "end markets.")
    assert classify(passage, "Apple Inc.") == ("unnamed", None, None)


def test_classify_unnamed_for_empty_passage():
    """Would fail if an empty passage raised instead of degrading to the
    same "unnamed" outcome as any other no-match input."""
    assert classify("", "Apple Inc.") == ("unnamed", None, None)


def test_classify_unnamed_for_empty_counterparty():
    """Would fail if an empty counterparty were treated as matching
    everything rather than naming nothing."""
    passage = "Direct sales to Apple Inc. accounted for 27.7% of our net sales."
    assert classify(passage, "") == ("unnamed", None, None)


def test_classify_amkor_percentage_comes_from_naming_sentence_not_window_opener():
    """The exact audited bug: Amkor's +/-420-char window opens with the
    aggregate "69%" for the ten largest customers, and only later reaches
    the Apple-specific "27.7%". `pct` must be "27.7", not the first
    percentage the window happens to contain. Would fail if `pct` were
    taken from a regex search over the whole passage instead of over just
    the naming sentence."""
    passage = ("Our ten largest customers accounted for 69% of net sales. "
               "Direct sales to Apple Inc. accounted for 27.7% of our net "
               "sales for the year ended December 31, 2023.")
    relation, sentence, pct = classify(passage, "Apple")
    assert relation == "customer"
    assert sentence == ("Direct sales to Apple Inc. accounted for 27.7% of "
                         "our net sales for the year ended December 31, 2023.")
    assert pct == "27.7"


def test_classify_pct_is_none_when_naming_sentence_has_no_percentage():
    """A naming sentence that carries no percentage figure must yield
    pct=None, not a percentage scavenged from elsewhere in the passage.
    Would fail if `pct` fell back to searching the rest of the passage
    when the naming sentence itself had no match."""
    passage = ("Our net sales grew 8% overall this year. Our key customers "
               "include Apple Inc. and Samsung Electronics.")
    relation, sentence, pct = classify(passage, "Apple Inc.")
    assert relation == "customer"
    assert pct is None
