-- News store for the co-mention relation edge (D-16's unbuilt half).
--
-- Lives in its own schema inside `orchestrator` so it can join against
-- audit_log's signals, while touching nothing that already exists.
--
-- Point-in-time by construction: `created_at` is the publication instant, and
-- every consumer filters on it. Nothing here records what we knew later.

CREATE SCHEMA IF NOT EXISTS lagmatrix;

CREATE TABLE IF NOT EXISTS lagmatrix.news_article (
    id           bigint      PRIMARY KEY,          -- Alpaca/Benzinga article id
    created_at   timestamptz NOT NULL,             -- publication instant
    updated_at   timestamptz,
    headline     text        NOT NULL,
    author       text,
    source       text,
    summary      text,
    url          text,
    ingested_at  timestamptz NOT NULL DEFAULT now()  -- when WE saw it, never a filter key
);

CREATE INDEX IF NOT EXISTS news_article_created_idx
    ON lagmatrix.news_article (created_at);

-- Many-to-many: one article tags several symbols. This is the table the
-- co-mention edge is actually built from.
CREATE TABLE IF NOT EXISTS lagmatrix.news_symbol (
    article_id bigint NOT NULL
        REFERENCES lagmatrix.news_article(id) ON DELETE CASCADE,
    symbol     text   NOT NULL,
    PRIMARY KEY (article_id, symbol)
);

CREATE INDEX IF NOT EXISTS news_symbol_symbol_idx
    ON lagmatrix.news_symbol (symbol);

-- Undirected co-mention pairs, ordered so each pair appears once. Deliberately
-- a view rather than a materialised table: callers must supply their own
-- `created_at < as_of` bound, which makes look-ahead impossible to introduce by
-- forgetting to. Materialising it would bake in a single as-of date.
CREATE OR REPLACE VIEW lagmatrix.news_comention AS
SELECT s1.symbol AS symbol_a,
       s2.symbol AS symbol_b,
       n.id      AS article_id,
       n.created_at
FROM lagmatrix.news_symbol s1
JOIN lagmatrix.news_symbol s2
  ON s1.article_id = s2.article_id AND s1.symbol < s2.symbol
JOIN lagmatrix.news_article n
  ON n.id = s1.article_id;

-- Coverage bookkeeping, so a resumed backfill knows what it already has and
-- an empty window is distinguishable from an unfetched one.
CREATE TABLE IF NOT EXISTS lagmatrix.news_coverage (
    symbol       text        NOT NULL,
    window_start date        NOT NULL,
    window_end   date        NOT NULL,
    n_articles   integer     NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, window_start, window_end)
);
