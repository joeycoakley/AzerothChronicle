# Azeroth Chronicle
## Development Specification
**Version:** 0.1  
**Status:** Initial development specification  
**Target:** World of Warcraft: Forever / compatible WoW Classic-style client  
**Primary use case:** Personal, spoiler-safe lore journal generated from the quests, NPCs, dialogue, books, and world events the player actually encounters.

---

## 1. Product Summary

Azeroth Chronicle is a World of Warcraft addon plus an optional local desktop companion.

The addon observes narrative events exposed by the WoW addon API and records an append-only journal of what the player has actually encountered. It does **not** depend on a prebuilt quest/lore database.

The companion imports the recorded journal, stores normalized/derived data in SQLite, reconstructs quest/story relationships, and optionally uses an LLM to generate summaries such as:

- "Catch me up on what I've been doing."
- "Who is this NPC again?"
- "Why am I helping this faction?"
- "What happened the last time I was in this zone?"
- "What do I currently know about the Defias?"
- "Which storylines am I in the middle of?"

The player's captured history defines the spoiler boundary. The LLM should only receive information the player has already encountered unless the user explicitly opts into external lore.

---

# 2. Design Principles

## 2.1 Capture first, interpret later

The addon should capture raw narrative events as faithfully as possible.

Do not make the LLM, storyline inference engine, or SQLite schema the source of truth.

Canonical flow:

```text
WoW events
   |
   v
Raw SavedVariables event journal
   |
   +--------------------+
   |                    |
   v                    v
In-game journal    Desktop companion
                        |
                        v
                     SQLite
                        |
             +----------+----------+
             |          |          |
             v          v          v
         entities    stories    summaries
                                  / search
```

## 2.2 Raw data is canonical

The authoritative player history is the addon's SavedVariables event journal.

Everything below is derived and may be deleted/rebuilt:

- SQLite tables
- NPC/quest relationship graphs
- inferred storylines
- embeddings
- vector search indexes
- summaries
- LLM-generated descriptions

This allows:
- moving between PCs,
- recovering from a deleted database,
- improving the parser later,
- regenerating summaries with a better model,
- sharing the addon with other players without sharing personal history.

## 2.3 Spoiler safety by construction

By default, Azeroth Chronicle may only summarize or reason over captured player knowledge.

A query such as:

> Who is Captain Alric?

must be answered using only:
- quests the player has seen,
- NPC dialogue the player has observed,
- books/items the player has opened,
- world dialogue the addon captured,
- prior summaries derived exclusively from those sources.

External lore is a separate future feature and must be clearly opt-in.

## 2.4 Local-first

V1 should not require:
- a hosted backend,
- user accounts,
- cloud sync,
- an external lore API,
- a web service operated by the developer.

The complete core experience should work with:
- WoW,
- the addon,
- a local companion,
- a local SQLite database,
- an optional user-configured LLM provider.

## 2.5 Shareable, but not SaaS

The architecture should support giving the project to a friend without redesigning it.

Avoid:
- hardcoded filesystem paths,
- hardcoded character names,
- developer-specific API keys,
- machine-specific database assumptions.

---

# 3. Scope

## 3.1 V0 — Capture Harness

Goal:

> Prove that the target WoW client exposes enough narrative information to reconstruct a player's journey.

V0 should:
- load successfully,
- register narrative events,
- capture quest data,
- capture relevant NPC/gossip text,
- capture quest completion/progress text,
- capture item/book text when available,
- capture selected NPC/world dialogue,
- persist events using SavedVariables,
- expose debugging commands,
- require no desktop companion,
- require no polished UI.

V0 is successful when a player can encounter a previously unknown quest, run `/reload`, inspect SavedVariables, and see sufficient structured information to reconstruct that interaction.

## 3.2 V1 — Chronicle Addon

Add:
- basic in-game journal,
- current story threads,
- recently encountered NPCs,
- recently completed quests,
- raw captured text viewer,
- capture enable/disable settings,
- diagnostic/export information.

## 3.3 V1 — Desktop Companion

Add:
- WoW installation discovery,
- SavedVariables discovery,
- importer,
- SQLite database,
- incremental event ingestion,
- entity normalization,
- storyline inference,
- LLM integration,
- "Catch me up" summaries,
- character/NPC/location Q&A,
- export/import,
- rebuild database.

## 3.4 Explicit non-goals for V1

Do not initially build:
- cloud accounts,
- cloud synchronization,
- community quest database,
- public website,
- rotation/combat coaching,
- automatic WoW input,
- screen scraping,
- network packet inspection,
- real-time HTTP calls from the addon,
- a complete externally sourced Warcraft lore corpus.

---

# 4. System Architecture

```text
+-------------------------------------------------------+
|                    WORLD OF WARCRAFT                  |
|                                                       |
|  Azeroth Chronicle Addon                              |
|                                                       |
|  Event listeners                                      |
|       |                                               |
|       v                                               |
|  Capture/normalization                                |
|       |                                               |
|       +-----------------------> In-game Chronicle UI  |
|       |                                               |
|       v                                               |
|  AzerothChronicleDB                                   |
|  SavedVariables                                       |
+--------------------------+----------------------------+
                           |
             written by WoW on /reload,
            logout, disconnect, or exit
                           |
                           v
+-------------------------------------------------------+
|              AZEROTH CHRONICLE COMPANION              |
|                                                       |
|  SavedVariables importer                              |
|       |                                               |
|       v                                               |
|  Event de-duplication                                 |
|       |                                               |
|       v                                               |
|  SQLite                                               |
|       |                                               |
|       +--------> Entity extraction                    |
|       +--------> Quest sequencing                     |
|       +--------> Storyline inference                  |
|       +--------> Search/index                         |
|       +--------> LLM context builder                  |
|                         |                             |
|                         v                             |
|                   LLM provider                        |
|                         |                             |
|                         v                             |
|                  Summaries / Q&A                      |
+-------------------------------------------------------+
```

Important limitation:

The WoW addon environment should be treated as sandboxed. V1 must not assume that Lua can POST directly to localhost or write arbitrary files. The supported bridge is SavedVariables.

---

# 5. WoW Addon Project Layout

Initial layout:

```text
AzerothChronicle/
|
|-- AzerothChronicle.toc
|-- Core.lua
|-- Database.lua
|
|-- Capture/
|   |-- Quest.lua
|   |-- Gossip.lua
|   |-- Dialogue.lua
|   `-- ItemText.lua
|
|-- Util/
|   |-- Identity.lua
|   |-- Location.lua
|   `-- Debug.lua
|
`-- UI/
    `-- Journal.lua
```

V0 may start with only:

```text
AzerothChronicle/
|-- AzerothChronicle.toc
`-- Core.lua
```

Refactor only after capture behavior is proven.

---

# 6. TOC / SavedVariables

Use a per-character history database.

Example:

```text
## Interface: <TARGET_CLIENT_INTERFACE>
## Title: Azeroth Chronicle
## Notes: Your personal record of the stories you discover in Azeroth.
## Version: 0.0.1
## SavedVariablesPerCharacter: AzerothChronicleDB

Core.lua
Database.lua
Capture\Quest.lua
Capture\Gossip.lua
Capture\Dialogue.lua
Capture\ItemText.lua
Util\Identity.lua
Util\Location.lua
Util\Debug.lua
UI\Journal.lua
```

The target interface number must be confirmed against the actual Forever/beta client instead of copied from another branch.

---

# 7. Event Capture

Forever-specific support must be experimentally validated.

The initial capture harness should register candidate events and log what actually works.

## 7.1 Quest lifecycle

Candidate events:

```text
QUEST_DETAIL
QUEST_ACCEPTED
QUEST_PROGRESS
QUEST_COMPLETE
QUEST_TURNED_IN
QUEST_FINISHED
QUEST_LOG_UPDATE
```

Desired capture:

### QUEST_DETAIL
Capture when available:
- quest ID
- title
- full quest description
- objective narrative
- quest giver name
- quest giver GUID
- player identity
- zone
- map ID
- coordinates
- timestamp
- session ID

### QUEST_ACCEPTED
Capture:
- quest ID
- timestamp
- quest log index if exposed
- source NPC when known

### QUEST_PROGRESS
Capture:
- quest ID
- NPC
- progress dialogue
- location
- timestamp

### QUEST_COMPLETE
Capture:
- quest ID
- NPC
- completion/reward dialogue
- location
- timestamp

### QUEST_TURNED_IN
Capture:
- quest ID
- completion timestamp
- XP reward if readily available
- money reward if readily available

Do not delay V0 to capture every reward field.

---

# 8. Gossip / NPC Capture

Candidate event:

```text
GOSSIP_SHOW
```

Capture when available:
- NPC name
- NPC GUID
- gossip text
- selectable gossip options
- available quests
- active quests
- location
- timestamp

Avoid storing the same gossip payload repeatedly when the player reopens the same NPC window within a short interval.

Deduplication may happen either:
- in-addon using a content hash, or
- later in the companion.

For V0, prefer capturing too much over accidentally losing unique content.

---

# 9. Item / Book / Journal Capture

Candidate events/APIs should be tested for:
- readable books,
- letters,
- signs,
- journal pages,
- quest items that expose narrative text.

Desired data:
- item identity
- item title/name
- page number if relevant
- displayed text
- timestamp
- location

Pages should be preserved individually rather than concatenated destructively.

---

# 10. World Dialogue Capture

Candidate events:

```text
CHAT_MSG_MONSTER_SAY
CHAT_MSG_MONSTER_YELL
CHAT_MSG_MONSTER_EMOTE
CHAT_MSG_MONSTER_WHISPER
```

Potential additional events should be discovered experimentally.

Capture:
- speaker
- text
- speaker GUID when available
- zone/location
- timestamp
- nearby/associated quest ID if one can be established without guessing

Important:

World chat can be noisy. V0 should capture it, but the companion should later apply filtering and relevance scoring.

---

# 11. Raw Event Model

Use an append-only event journal.

Example:

```lua
{
    id = "event-id",
    schemaVersion = 1,
    sessionId = "session-id",

    character = {
        guid = "Player-...",
        name = "CharacterName",
        realm = "RealmName",
        class = "MAGE",
        level = 18
    },

    type = "QUEST_DETAIL",
    timestamp = 1790000000,

    location = {
        zone = "Westfall",
        mapId = 52,
        x = 0.42,
        y = 0.71
    },

    source = {
        guid = "Creature-...",
        name = "Marshal Gryan Stoutmantle"
    },

    quest = {
        id = 12345,
        title = "The Defias Brotherhood",
        description = "...",
        objectivesText = "..."
    }
}
```

Not every event contains every field.

---

# 12. Event Identity

Every event needs a stable unique identity so histories can eventually be merged across machines.

Recommended conceptual identity:

```text
<CharacterGUID>:<SessionID>:<Sequence>
```

Example:

```text
Player-123-ABC:20260913T153000Z-a91f:000042
```

Requirements:
- unique per event,
- generated without a network dependency,
- deterministic sequence within a session,
- preserved forever after creation.

Do not use timestamp alone as an event key.

---

# 13. Session Identity

Generate a session identifier on addon initialization.

A session represents one addon runtime between load and logout/reload.

Example:

```lua
session = {
    id = "...",
    startedAt = ...,
    addonVersion = "0.0.1",
    schemaVersion = 1,
    clientBuild = "...",
    interfaceVersion = ...
}
```

This is useful for:
- debugging,
- duplicate detection,
- beta API comparison,
- imports,
- migration,
- eventual multi-PC synchronization.

---

# 14. SavedVariables Schema

Recommended top-level layout:

```lua
AzerothChronicleDB = {
    schemaVersion = 1,

    metadata = {
        createdAt = ...,
        addonVersion = "...",
    },

    character = {
        guid = "...",
        name = "...",
        realm = "...",
    },

    sessions = {
        ...
    },

    events = {
        ...
    },

    settings = {
        captureWorldDialogue = true,
        captureGossip = true,
        captureItemText = true,
        debug = false
    }
}
```

Do not initially store LLM summaries in the canonical journal.

---

# 15. Storage Growth

Quest text and dialogue can accumulate significantly over months.

V0:
- optimize for correctness, not compression.

Later:
- avoid duplicate identical gossip entries,
- avoid duplicate quest-detail captures,
- normalize repeated strings if needed,
- optionally archive old raw events,
- measure actual SavedVariables size before optimizing.

Do not introduce compression/encryption in the addon until necessary.

---

# 16. SQLite Companion Data Model

SQLite is a **materialized local index**, not the authoritative history.

Initial tables:

```text
characters
sessions
events
quests
quest_encounters
npcs
npc_encounters
dialogue
locations
items
item_text
quest_relationships
storylines
storyline_members
summaries
import_state
```

## 16.1 events

Fields:

```text
event_id TEXT PRIMARY KEY
schema_version INTEGER
session_id TEXT
character_id TEXT
event_type TEXT
timestamp INTEGER
zone TEXT
map_id INTEGER
x REAL
y REAL
raw_payload_json TEXT
imported_at INTEGER
```

Preserve the original payload.

## 16.2 quests

Fields:

```text
quest_id INTEGER
character_id TEXT
title TEXT
first_seen_at INTEGER
accepted_at INTEGER
completed_at INTEGER
giver_npc_id TEXT
description TEXT
objectives_text TEXT
completion_text TEXT
PRIMARY KEY (character_id, quest_id)
```

## 16.3 npcs

Fields:

```text
npc_key TEXT PRIMARY KEY
display_name TEXT
first_seen_at INTEGER
first_zone TEXT
```

NPC keys should prefer stable GUID-derived identity where feasible rather than display name alone.

## 16.4 dialogue

Fields:

```text
dialogue_id TEXT PRIMARY KEY
event_id TEXT
npc_key TEXT
quest_id INTEGER NULL
dialogue_type TEXT
text TEXT
timestamp INTEGER
```

## 16.5 quest_relationships

Fields:

```text
from_quest_id INTEGER
to_quest_id INTEGER
relationship_type TEXT
confidence REAL
evidence_json TEXT
```

Relationship types may include:

```text
observed_immediate_followup
same_npc_sequence
explicit_reference
inferred_chain
manual
```

---

# 17. Companion Import Pipeline

```text
Find WoW
   |
   v
Find AzerothChronicle SavedVariables
   |
   v
Parse Lua data
   |
   v
Read event IDs
   |
   v
Compare with import_state
   |
   v
Insert new events only
   |
   v
Normalize/materialize entities
   |
   v
Update story graph
```

Requirements:
- import must be idempotent,
- reimporting the same file must not duplicate data,
- a deleted SQLite DB must be rebuildable,
- malformed events must be logged and skipped rather than destroying the import.

---

# 18. Laptop / Multi-PC Portability

## V1

Support explicit:

```text
Export Journey
Import Journey
```

Export should contain the raw canonical history, not only SQLite.

Possible format:

```text
azeroth-chronicle-export/
|-- manifest.json
`-- events.jsonl
```

This avoids tying backup portability to the WoW Lua serialization format.

## Manual migration

A user may also move/copy the relevant WoW SavedVariables file.

SQLite should not need to be copied.

## Future sync

Do not synchronize a live SQLite database through Dropbox/OneDrive.

If automatic multi-PC sync is later built, sync immutable events by event ID and let each PC maintain its own local SQLite materialization.

---

# 19. Companion Configuration

Initial configuration:

```text
WoW installation path
Character(s)
Database location
LLM provider
LLM model
API key / local endpoint
Enable AI features
```

Secrets:
- never store an API key in the WoW addon,
- store API configuration only in the companion,
- preferably use the operating system's native credential store later.

---

# 20. LLM Architecture

The LLM must not receive the entire player history for every query.

Use retrieval.

```text
User question
     |
     v
Intent classification
     |
     v
Relevant entities/story threads
     |
     v
Retrieve captured source material
     |
     v
Build spoiler-safe context
     |
     v
LLM
```

Example question:

> Who is Gryan again?

Context should include only:
- Gryan NPC encounters,
- quests involving Gryan that the player has encountered,
- relevant dialogue,
- already-derived summaries that cite only encountered sources.

LLM instruction:

```text
Answer only from the supplied player-history context.
Do not introduce Warcraft lore that is absent from the context.
If the player's captured history is insufficient, say that the
player has not learned enough yet.
```

---

# 21. Generated Summary Types

Initial summary functions:

## Catch Me Up — Global

```text
What have I been doing recently?
What are my active story threads?
What unresolved things should I remember?
```

## Zone Recap

```text
What has happened to me in Westfall?
Who have I met here?
What storylines am I following here?
```

## Quest Context

```text
Why am I doing this quest?
What led to it?
Who are the relevant characters?
```

## NPC Context

```text
Who is this NPC?
Where did I meet them?
What have they asked me to do?
```

## Faction Context

```text
What has my character learned about this faction?
```

---

# 22. Storyline Inference

Do not require a prebuilt quest-chain database.

Infer probable relationships from observations.

High-confidence examples:

```text
Quest A turned in
same NPC immediately offers Quest B
=> probable A -> B
```

```text
Quest A completion dialogue names NPC B
player reaches NPC B and accepts Quest C
=> probable contextual relationship
```

Every inferred relationship should contain:
- relationship type,
- confidence,
- evidence,
- creation algorithm/version.

Never silently promote inferred relationships to canonical facts.

---

# 23. In-Game UI

## V0

No polished UI required.

Commands:

```text
/ac status
/ac debug on
/ac debug off
/ac stats
/ac last
```

Potential output:

```text
Azeroth Chronicle
Character: Kaelthas - Realm
Session: 9f13...
Events this session: 42
Quests captured: 7
Gossip entries: 16
World dialogue: 19
```

## V1 Journal

Primary window:

```text
+----------------------------------------------------------------+
| AZEROTH CHRONICLE                                        [ X ] |
+---------------+--------------------------------+---------------+
| JOURNEY       |                                | RELATED       |
|               |  The Defias Brotherhood       |               |
| Elwynn        |                                | Characters    |
| Westfall      |  What you've learned           | Locations     |
| Duskwood      |                                | Factions      |
|               |  Your journey                  |               |
|               |                                |               |
+---------------+--------------------------------+---------------+
|                         Catch Me Up                         |
+----------------------------------------------------------------+
```

Suggested tabs:

```text
Journey
Quests
Characters
Factions
Locations
Recent
```

The first UI should primarily use Blizzard-native frame templates, textures, fonts, and atlases.

Custom art is optional and should come later.

---

# 24. In-Game Notifications

Keep them subtle.

Examples:

```text
Azeroth Chronicle: Quest recorded
Azeroth Chronicle: New character encountered — Marshal Gryan
Azeroth Chronicle: Lore entry updated
```

Allow disabling notifications independently from capture.

Avoid interrupting gameplay.

---

# 25. Debugging / Beta Discovery Mode

A key feature for initial Forever development.

Add:

```text
/ac discovery on
```

Discovery mode should:
- log candidate narrative events,
- log event payloads,
- show which APIs returned data,
- record client build/interface number,
- preserve unknown event payloads where practical.

Example debug:

```text
[AC] QUEST_DETAIL
questID=90123
title="..."
giver="..."
GetQuestText=yes
GetObjectiveText=yes
map=...
```

This mode exists specifically to discover differences between Forever and current Classic APIs.

---

# 26. API Compatibility Layer

Do not scatter direct API calls throughout the addon.

Create wrappers.

Example:

```lua
AC.API.GetGossipText()
AC.API.GetCurrentQuestID()
AC.API.GetQuestDescription()
AC.API.GetQuestRewardText()
```

Wrapper logic may detect which API exists:

```lua
if C_GossipInfo and C_GossipInfo.GetText then
    ...
elseif GetGossipText then
    ...
end
```

This is important because WoW branches frequently expose different generations of an API.

---

# 27. Error Handling

The addon must never break quest interaction because capture code failed.

Every capture path should:
- tolerate nil values,
- avoid throwing from an event handler,
- skip unavailable fields,
- record debug diagnostics,
- keep gameplay functional.

Conceptually:

```lua
local ok, err = pcall(CaptureQuest)
if not ok then
    AC.Debug.Error("QUEST_DETAIL", err)
end
```

---

# 28. Privacy

Default:
- all data local,
- no telemetry to developer,
- no cloud upload,
- no automatic contribution to a community dataset.

If community contribution is ever implemented, it must be opt-in and should separate:
- canonical game content,
from:
- player identity/history.

---

# 29. Development Environment

Recommended:
- VS Code
- Git
- private GitHub repository
- Lua language tooling
- WoW API autocomplete/type definitions
- SQLite browser/tooling for companion work

Repository layout:

```text
azeroth-chronicle/
|
|-- addon/
|   `-- AzerothChronicle/
|
|-- companion/
|   |-- src/
|   |-- tests/
|   `-- migrations/
|
|-- docs/
|   `-- development-spec.md
|
|-- samples/
|   `-- savedvariables/
|
|-- .gitignore
`-- README.md
```

Do not commit:
- API keys,
- real player account identifiers,
- entire WTF directories,
- generated SQLite databases unless specifically sanitized test fixtures.

---

# 30. Recommended Companion Technology

For the first version:

```text
Python
SQLite
Pydantic/dataclasses
Typer or argparse CLI
```

Later UI options:
- PySide6,
- Tauri + small local service,
- another packaged desktop framework.

Do not spend V0 time choosing a polished desktop UI framework.

Start with:

```text
azeroth-chronicle import
azeroth-chronicle status
azeroth-chronicle quests
azeroth-chronicle recap
```

---

# 31. First Development Milestone

## Milestone 0 — Hello Chronicle

Acceptance criteria:

- addon appears in WoW addon list,
- `/reload` loads updated code,
- `ADDON_LOADED` initializes SavedVariables,
- `/ac status` works.

## Milestone 1 — Capture One Quest

Acceptance criteria:

After opening a quest, SavedVariables contains:
- event ID,
- quest ID,
- title,
- quest text,
- NPC identity if available,
- timestamp,
- zone,
- client/session metadata.

## Milestone 2 — Capture Full Quest Lifecycle

Acceptance criteria:

For one quest:
- detail observed,
- accepted,
- progress dialogue captured,
- completion text captured,
- turned-in event captured.

## Milestone 3 — Capture Non-Quest Lore

Acceptance criteria:
- gossip captured,
- one readable item/book captured,
- one NPC/world dialogue event captured.

## Milestone 4 — Import

Acceptance criteria:
- companion locates or is pointed at SavedVariables,
- imports events to SQLite,
- repeat import creates zero duplicates,
- database can be deleted and fully rebuilt.

## Milestone 5 — First AI Recap

Acceptance criteria:

Given only captured material, companion can generate:

> "Catch me up on my last session."

No external lore is supplied to the model.

---

# 32. Initial Sprint Backlog

Recommended order:

1. Create Git repository.
2. Create addon folder and TOC.
3. Add `ADDON_LOADED`.
4. Initialize `AzerothChronicleDB`.
5. Add session metadata.
6. Add event ID generator.
7. Implement `/ac status`.
8. Register `QUEST_DETAIL`.
9. Dump every available quest-related field.
10. Save the raw event.
11. Verify persistence after `/reload`.
12. Register remaining quest lifecycle events.
13. Add gossip capture.
14. Add item text capture.
15. Add world dialogue capture.
16. Collect beta samples.
17. Freeze raw event schema v1.
18. Start companion importer.
19. Add SQLite migrations.
20. Build first local recap.

---

# 33. Beta Validation Checklist

On the first Forever/beta session:

## Environment

- [ ] Record client build.
- [ ] Record interface version.
- [ ] Confirm addon loads.
- [ ] Confirm SavedVariables works.
- [ ] Confirm `/reload` persists data.

## Quest APIs

- [ ] QUEST_DETAIL fires.
- [ ] Current quest ID available.
- [ ] Quest title available.
- [ ] Quest description available.
- [ ] Objective narrative available.
- [ ] Quest NPC token/name available.
- [ ] QUEST_ACCEPTED fires.
- [ ] QUEST_PROGRESS fires.
- [ ] Progress text available.
- [ ] QUEST_COMPLETE fires.
- [ ] Completion/reward text available.
- [ ] QUEST_TURNED_IN fires.

## NPC / Lore

- [ ] GOSSIP_SHOW fires.
- [ ] Gossip text available.
- [ ] Gossip options available.
- [ ] Readable item/book events available.
- [ ] Book page text available.
- [ ] NPC say/yell events available.
- [ ] Speaker identity available.

## Location

- [ ] Zone available.
- [ ] Map ID available.
- [ ] Player coordinates available.

Any API difference should be handled in the compatibility layer rather than modifying capture logic everywhere.

---

# 34. Definition of V1 Success

Azeroth Chronicle V1 is successful when:

1. A new player can install the addon without a prebuilt lore database.
2. The addon records the narrative content they actually encounter.
3. Their history survives logout/reload.
4. Their local database can be rebuilt from raw history.
5. They can ask "catch me up" and receive a useful spoiler-safe recap.
6. They can ask about an encountered NPC/quest/location and get an answer grounded in their own history.
7. Moving to another PC requires transferring/importing canonical history, not preserving a fragile SQLite file.
8. A friend can install the same software and maintain an entirely separate personal Chronicle.

---

# 35. Portability Recommendation for Development

For the specification and source code, use a **private Git repository** as soon as development begins.

Recommended workflow:

```text
Desktop
   |
git commit / git push
   |
Private GitHub repository
   |
git pull
   |
Laptop
```

Email is perfectly adequate for transferring this initial specification, but it becomes error-prone once multiple Lua/Python files exist.

For personal game-history backup, use explicit export/import or synchronize exported event files. Do not treat a live SQLite file as the sync primitive.

---

# 36. Key Technical Assumptions / Risks

## Forever API compatibility

The target client's exact addon API must be validated against the actual beta/client.

Current Classic-family APIs provide the basic primitives required for:
- quest detail events,
- gossip text,
- quest completion/reward text,
- SavedVariables persistence.

Forever may differ.

Mitigation:
- discovery/debug mode,
- compatibility wrapper layer,
- capture raw payloads,
- beta validation before building higher-level features.

## SavedVariables are not real-time IPC

The addon cannot assume continuous external streaming.

SavedVariables are written when WoW:
- reloads the UI,
- logs out,
- disconnects,
- exits.

Mitigation:
- in-game experience reads memory directly,
- companion imports after a write boundary,
- design V1 recaps around session boundaries,
- investigate only documented/supported mechanisms for anything more immediate.

## Source text availability

Some narrative content may be rendered through UI systems that expose less text than expected.

Mitigation:
- discover during beta,
- capture multiple event sources,
- preserve unknown/raw fields,
- do not promise complete lore capture until validated.

---

# 37. Reference Notes

Useful API/documentation starting points:

- Warcraft Wiki — SavedVariables  
  https://warcraft.wiki.gg/wiki/SavedVariables

- Warcraft Wiki — Saving variables between game sessions  
  https://warcraft.wiki.gg/wiki/Saving_variables_between_game_sessions

- Warcraft Wiki — QUEST_DETAIL  
  https://warcraft.wiki.gg/wiki/Event:QUEST_DETAIL

- Warcraft Wiki — C_GossipInfo.GetText  
  https://warcraft.wiki.gg/wiki/API:C_GossipInfo.GetText

- Warcraft Wiki — GetRewardText  
  https://warcraft.wiki.gg/wiki/API:GetRewardText

- Warcraft Wiki — UI Beginners Guide  
  https://warcraft.wiki.gg/wiki/UI_Beginners_Guide

---

# 38. Immediate Next Step

Do not start with SQLite or the LLM.

Create:

```text
addon/AzerothChronicle/
|-- AzerothChronicle.toc
`-- Core.lua
```

Implement:

```text
ADDON_LOADED
/ac status
QUEST_DETAIL
append-only SavedVariables event
```

Then prove this statement:

> "I can open a quest that Azeroth Chronicle has never seen before and preserve enough information to understand it later."

Once that works, continue outward from the raw capture layer.
