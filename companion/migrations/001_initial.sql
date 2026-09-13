-- Azeroth Chronicle companion, initial schema.
--
-- This database is a materialized index over the addon's raw event
-- journal, never the source of truth (spec section 16). Every table below
-- except events and import_state is derived and can be dropped and
-- rebuilt from the events table alone. The events table in turn can be
-- rebuilt from the SavedVariables files.
--
-- Because of that, correctness beats cleverness here: no destructive
-- normalization, and the original payload is kept verbatim on every row.

CREATE TABLE IF NOT EXISTS characters (
    character_id    TEXT PRIMARY KEY,   -- player GUID
    name            TEXT,
    realm           TEXT,
    class           TEXT,
    level_last_seen INTEGER,
    first_seen_at   INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    character_id      TEXT,
    started_at        INTEGER,
    addon_version     TEXT,
    schema_version    INTEGER,
    client_version    TEXT,
    client_build      TEXT,
    interface_version INTEGER,
    -- Which required APIs the client did not expose during this session.
    -- Lets a later query tell "the client could not report this" apart
    -- from "the player never did this".
    missing_apis_json TEXT,
    FOREIGN KEY (character_id) REFERENCES characters(character_id)
);

-- The one table that matters. Everything else is rebuilt from here.
CREATE TABLE IF NOT EXISTS events (
    event_id         TEXT PRIMARY KEY,
    schema_version   INTEGER,
    session_id       TEXT,
    character_id     TEXT,
    event_type       TEXT,
    timestamp        INTEGER,
    zone             TEXT,
    subzone          TEXT,
    map_id           INTEGER,
    x                REAL,
    y                REAL,
    raw_payload_json TEXT NOT NULL,
    imported_at      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_character ON events(character_id);

CREATE TABLE IF NOT EXISTS npcs (
    -- Derived from the creature id inside the GUID rather than the whole
    -- GUID, because the full GUID encodes a specific spawn and differs
    -- between two sightings of the same character. Falls back to the
    -- display name when no GUID was captured.
    npc_key       TEXT PRIMARY KEY,
    display_name  TEXT,
    first_seen_at INTEGER,
    first_zone    TEXT
);

CREATE TABLE IF NOT EXISTS quests (
    character_id    TEXT NOT NULL,
    quest_id        INTEGER NOT NULL,
    title           TEXT,
    first_seen_at   INTEGER,
    accepted_at     INTEGER,
    completed_at    INTEGER,
    turned_in_at    INTEGER,
    giver_npc_key   TEXT,
    description     TEXT,
    objectives_text TEXT,
    completion_text TEXT,
    xp_reward       INTEGER,
    money_reward    INTEGER,
    PRIMARY KEY (character_id, quest_id)
);

-- One row per observed narrative utterance, whatever its source. Gossip,
-- quest progress text and monster chat all land here so a recap can read
-- a single ordered stream of what the player was actually told.
CREATE TABLE IF NOT EXISTS dialogue (
    dialogue_id   TEXT PRIMARY KEY,
    event_id      TEXT NOT NULL,
    character_id  TEXT,
    npc_key       TEXT,
    quest_id      INTEGER,
    dialogue_type TEXT,
    text          TEXT,
    timestamp     INTEGER,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE INDEX IF NOT EXISTS idx_dialogue_npc ON dialogue(npc_key);
CREATE INDEX IF NOT EXISTS idx_dialogue_quest ON dialogue(quest_id);

CREATE TABLE IF NOT EXISTS item_text (
    item_text_id TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL,
    character_id TEXT,
    item_name    TEXT,
    page         INTEGER,
    text         TEXT,
    creator      TEXT,
    timestamp    INTEGER,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

-- Tracks what has already been ingested so a repeat import is a no-op.
-- Keyed by source path; the event ids themselves are the real guard
-- against duplicates, this is for reporting and for skipping unchanged
-- files cheaply.
CREATE TABLE IF NOT EXISTS import_state (
    source_path       TEXT PRIMARY KEY,
    last_imported_at  INTEGER,
    last_file_mtime   REAL,
    last_file_size    INTEGER,
    events_seen       INTEGER,
    events_inserted   INTEGER,
    events_skipped    INTEGER
);
