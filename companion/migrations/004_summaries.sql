-- Generated summaries.
--
-- Derived, like every other table here except events: a summary can be
-- deleted and regenerated, and should be when a better model exists. That
-- is why the source event ids are stored alongside the text. A summary that
-- cannot say what it was built from cannot be audited, and this one makes a
-- specific promise worth auditing: that nothing in it came from outside the
-- player's own captured history.

CREATE TABLE IF NOT EXISTS summaries (
    summary_id            TEXT PRIMARY KEY,
    character_id          TEXT,
    kind                  TEXT,
    window_start          INTEGER,
    window_end            INTEGER,
    model                 TEXT,
    created_at            INTEGER,
    -- Hash of the assembled context. Identical input means the summary
    -- would be the same, so there is no reason to pay for it twice.
    context_hash          TEXT,
    text                  TEXT,
    source_event_ids_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_summaries_window ON summaries(window_start);
CREATE INDEX IF NOT EXISTS idx_summaries_hash ON summaries(context_hash);
