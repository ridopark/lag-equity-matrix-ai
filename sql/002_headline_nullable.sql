-- Benzinga ships articles with no headline (e.g. id 11415383, 2018-03-26).
-- The NOT NULL was my assumption, not a property of the data.
--
-- Dropping such rows would be worse than keeping them: the headline is not what
-- the co-mention edge reads. The symbol tags are, and those are intact. An
-- article we cannot label is still an article that links two tickers.
ALTER TABLE lagmatrix.news_article ALTER COLUMN headline DROP NOT NULL;
