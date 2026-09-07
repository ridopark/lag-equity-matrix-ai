-- Directed company-to-company edges extracted from 10-K text.
--
-- Unlike correlation (symmetric) and co-mention (undirected), these have a
-- direction: the FILER discloses a relationship to a NAMED counterparty, and
-- the disclosure sits in the filer's own document. QRVO names Apple; Apple
-- does not name QRVO. That asymmetry is the point -- it is what makes a
-- lead-lag hypothesis expressible at all.
--
-- Design note. `passage` is the durable artefact and `relation`/`pct_revenue`
-- are the fallible interpretation of it. Storing the raw text means a better
-- extractor can re-label later without re-crawling EDGAR, and means every edge
-- is auditable against the filing that produced it.
--
-- Point-in-time: `filing_date` is when the disclosure became public. Nothing
-- here may be used before that date.

CREATE TABLE IF NOT EXISTS lagmatrix.filing_mention (
    id             bigserial PRIMARY KEY,
    filer_ticker   text        NOT NULL,
    filer_cik      text        NOT NULL,
    accession      text        NOT NULL,
    filing_date    date        NOT NULL,      -- the point-in-time key
    form           text        NOT NULL,
    counterparty   text        NOT NULL,      -- name as written in the filing
    cp_ticker      text,                      -- resolved, NULL when unresolvable
    relation       text,                      -- customer | competitor | supplier | unknown
    confidence     text,                      -- heuristic | llm | manual
    pct_revenue    numeric,                   -- when the passage states one
    passage        text        NOT NULL,      -- the evidence, kept verbatim
    ingested_at    timestamptz NOT NULL DEFAULT now()
);

-- Expression uniqueness needs an index, not a table constraint. md5(passage)
-- rather than passage itself: the text can exceed the btree row limit.
CREATE UNIQUE INDEX IF NOT EXISTS filing_mention_uniq
    ON lagmatrix.filing_mention (accession, counterparty, md5(passage));

CREATE INDEX IF NOT EXISTS filing_mention_filer_idx  ON lagmatrix.filing_mention (filer_ticker);
CREATE INDEX IF NOT EXISTS filing_mention_cp_idx     ON lagmatrix.filing_mention (cp_ticker);
CREATE INDEX IF NOT EXISTS filing_mention_date_idx   ON lagmatrix.filing_mention (filing_date);

-- Which filings we have already walked, so a resumed crawl skips them and an
-- empty filing is distinguishable from an unvisited one.
CREATE TABLE IF NOT EXISTS lagmatrix.filing_coverage (
    accession    text PRIMARY KEY,
    filer_ticker text        NOT NULL,
    filing_date  date        NOT NULL,
    n_mentions   integer     NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now()
);

-- The directed edge, restricted to what we currently believe is a customer
-- relationship. A view, so re-labelling `relation` reshapes the graph without
-- a migration, and so callers must bound `filing_date` themselves.
CREATE OR REPLACE VIEW lagmatrix.supply_edge AS
SELECT filer_ticker AS supplier,
       cp_ticker    AS customer,
       pct_revenue,
       filing_date,
       confidence,
       passage
FROM lagmatrix.filing_mention
WHERE relation = 'customer' AND cp_ticker IS NOT NULL;
