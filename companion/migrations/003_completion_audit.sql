-- The client can confirm whether this character completed a given quest,
-- even when the addon never witnessed the turn-in: a quest handed in before
-- the addon existed, or during a session whose events were lost.
--
-- Kept separate from turned_in_at on purpose. That column means "we watched
-- this happen, at this moment". This one means "the client says it happened,
-- and we do not know when". Collapsing them would invent a timestamp.
ALTER TABLE quests ADD COLUMN known_complete INTEGER DEFAULT 0;
