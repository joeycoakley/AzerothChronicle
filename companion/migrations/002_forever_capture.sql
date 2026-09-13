-- Support for the capture added ahead of the WoW Forever beta.
--
-- Four gaps drove this: multi-quest NPCs greet through their own frame and
-- were captured as silence, abandoned quests stayed open forever, quests
-- already in the log at install were invisible, and arriving in a zone for
-- the first time went unrecorded. Character race and faction were missing
-- outright, which matters more with a new playable race shipping.

-- Race decides nothing mechanical here, but faction decides which version of
-- a quest's text the player was shown, so a summary that ignores it can
-- describe a story the character never saw.
ALTER TABLE characters ADD COLUMN race TEXT;
ALTER TABLE characters ADD COLUMN faction TEXT;

-- Set only when a quest left the log with no turn-in recorded. The client
-- fires the same event for abandoning and for completing, so this column is
-- an inference and is left NULL whenever the evidence is ambiguous.
ALTER TABLE quests ADD COLUMN abandoned_at INTEGER;

-- True when the quest was already in the log at the time the addon first
-- saw it, rather than observed being accepted. Those quests have no
-- description or giver, and a recap should not imply otherwise.
ALTER TABLE quests ADD COLUMN known_from_snapshot INTEGER DEFAULT 0;

-- First arrival only. Re-entry is constant and carries no narrative weight,
-- so this table stays small and each row marks a real moment in the journey.
CREATE TABLE IF NOT EXISTS zones (
    zone          TEXT NOT NULL,
    character_id  TEXT NOT NULL,
    map_id        INTEGER,
    subzone       TEXT,
    discovered_at INTEGER,
    event_id      TEXT,
    PRIMARY KEY (character_id, zone)
);

CREATE INDEX IF NOT EXISTS idx_zones_discovered ON zones(discovered_at);
