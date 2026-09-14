-- Supports zone-based chronicle chapters, alongside the existing
-- session recaps in the same `summaries` table (kind = 'zone_chapter'
-- vs 'session_recap').
--
-- Stored directly rather than derived from context text each time, since
-- a chapter needs to be looked up by zone (has this zone's chapter been
-- written yet? does it need updating?), and reverse-engineering a zone
-- name out of generated prose would be fragile where a plain column is not.
-- NULL for every session_recap row - not every kind of summary has one.
ALTER TABLE summaries ADD COLUMN zone TEXT;

CREATE INDEX IF NOT EXISTS idx_summaries_zone ON summaries(zone);
