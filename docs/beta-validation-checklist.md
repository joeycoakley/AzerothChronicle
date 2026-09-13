# Beta Validation Checklist

Derived from development-spec.md section 33, made concrete for an actual play
session. Anything that fails becomes a fix in the API compatibility layer in
`Core.lua`, not a change scattered through capture logic.

Run `/ac discovery on` before starting so each event prints what it found, and
`/ac apis` once per new client to see which APIs resolved at all.

Status below reflects the Classic Era 1.15.9 bring-up run on 2026-09-13,
character Syladre, quest 456 "The Balance of Nature" in Shadowglen.

## Confirmed findings

Settled on Classic Era. Re-verify each against Forever when that client exists.

| Finding | Value |
| --- | --- |
| QUEST_ACCEPTED argument 1 | quest log index (observed 2) |
| QUEST_ACCEPTED argument 2 | quest ID (observed 456) |
| `math.randomseed` | not exposed by the addon sandbox, calling it fails at runtime |
| `C_Map.GetBestMapID` | does not exist; the real call is `C_Map.GetBestMapForUnit` |
| NPC GUID format | `Creature-0-5165-1-35-2079-00002018B8`, where 2079 is the stable creature id |
| Gossip active quests | structured tables including questID, title, isComplete |
| Quest description newlines | preserved as `\n\n` through Lua serialization |
| Legacy gossip globals | `GetGossipText` and friends are absent; C_GossipInfo is the only gossip path |
| Required API coverage | 19 of 19 resolved on build 69722 |
| Session id format in practice | `20260913T155600Z-7f4f` |

The map API mistake is the one worth remembering. A wrapper guarded by
`if C_Map.GetBestMapID then` around a function that does not exist fails
completely silently: no error, no debug output, just a missing field in every
event forever. Run `/ac apis` on any new client to catch that class of problem
immediately, and check the `missingApis` list recorded in each session entry
when the data looks thin.

## Environment

| Check | Observed |
| --- | --- |
| Client version reported by `GetBuildInfo` | 1.15.9 |
| Client build number | 69722 |
| Interface version | 11509 |
| Addon appears in the character-select addon list | yes |
| Addon prints its load message on login | yes |
| `/ac status` prints a session id | yes |
| `/reload` persists events to disk | yes, 5 events and 1 session written |

An empty `events` table on disk alongside a non-zero `/ac status` count does not
mean capture failed. WoW writes SavedVariables only on reload, logout,
disconnect, or exit, so in-memory events are absent from the file until then.

## Quest APIs

A quest with a progress dialogue (one that makes you return before the objective
is done) exercises `QUEST_PROGRESS`, which otherwise never fires.

| Check | Observed |
| --- | --- |
| QUEST_DETAIL fires | yes |
| Quest ID available (`GetQuestID`) | yes, 456 |
| Quest title available (`GetTitleText`) | yes |
| Quest description available (`GetQuestText`) | yes, full multi-paragraph text |
| Objective narrative available (`GetObjectiveText`) | yes |
| Quest giver name available (`questnpc` or `npc` unit token) | yes, Conservator Ilthalaine |
| Quest giver GUID available | yes |
| QUEST_ACCEPTED fires | yes |
| QUEST_ACCEPTED argument 1 meaning | quest log index |
| QUEST_ACCEPTED argument 2 present | yes, the quest id |
| QUEST_PROGRESS fires | yes |
| Progress text available (`GetProgressText`) | yes |
| QUEST_COMPLETE fires | not yet exercised |
| Completion/reward text available (`GetRewardText`) | not yet exercised |
| QUEST_TURNED_IN fires | not yet exercised |
| XP reward argument present | not yet exercised |
| Money reward argument present | not yet exercised |

Repeated QUEST_PROGRESS events are expected and were observed twice with
identical text, because progress dialogue re-fires each time you talk to the
NPC. V0 keeps both. Collapsing them is companion-side work, not addon work.

## NPC and lore

| Check | Observed |
| --- | --- |
| GOSSIP_SHOW fires | yes |
| Gossip text available | yes |
| Which API returned it | `C_GossipInfo`, confirmed by the returned table shape |
| Gossip options available | empty for this NPC, needs one with dialogue options |
| Option shape (table of objects, or flat pairs) | not yet observed |
| Active quests list available | yes, structured |
| Available quests list available | empty for this NPC, needs a quest giver with an unaccepted quest |
| Reopening the same NPC within 30s is deduplicated | not yet exercised |
| ITEM_TEXT_READY fires on a readable book | not yet exercised |
| Book text available (`ItemTextGetText`) | not yet exercised |
| Book title available (`ItemTextGetItem`) | not yet exercised |
| Page number available (`ItemTextGetPage`) | not yet exercised |
| Turning a page produces a second event with the next page number | not yet exercised |
| CHAT_MSG_MONSTER_SAY fires | not yet exercised |
| CHAT_MSG_MONSTER_YELL fires | not yet exercised |
| Speaker name available | not yet exercised |
| Speaker GUID present at argument position 12 | not yet exercised |

Readable objects worth testing near Shadowglen: the training dummies area signs,
and quest letters. Further afield, the bookshelves in Stormwind Keep's library.

## Location

| Check | Observed |
| --- | --- |
| Zone name available (`GetZoneText`) | yes, "Teldrassil" |
| Subzone available (`GetSubZoneText`) | yes, "Shadowglen" |
| Map ID available (`C_Map.GetBestMapForUnit`) | yes, 1438 for Teldrassil |
| Player coordinates available (`C_Map.GetPlayerMapPosition`) | yes, normalized 0-1 (observed 0.587, 0.443) |
| Coordinates nil indoors or in instances? | not yet exercised |

## Milestone acceptance

- [x] Milestone 0: addon loads, SavedVariables initializes, `/ac status` works.
- [x] Milestone 1: quest 456 produced an event with id, quest id, title, full quest text, objectives, NPC name and GUID, timestamp, zone, and session metadata.
- [ ] Milestone 2: detail, accepted, and progress captured. Complete and turned-in still needed.
- [ ] Milestone 3: gossip captured. Readable book and world dialogue still needed.

## Next session

1. Finish quest 456 and turn it in, to close out Milestone 2. This is the
   only remaining path that exercises `GetRewardText` and the XP and money
   reward arguments.
2. Read a book or sign, and catch an NPC say or yell, to close out Milestone 3.
3. Talk to a quest giver with an unaccepted quest, so gossip options and
   available quests come back populated rather than empty.
4. Step indoors and check whether coordinates go nil, since the companion
   must treat a missing position as normal rather than as corrupt data.

## After the run

1. Copy the SavedVariables file into `samples/savedvariables/` with account
   identifiers and character names stripped or replaced. This becomes the
   fixture the companion importer is built against.
2. Record any API that behaved differently than assumed, and fix it in the
   `AC.API` wrappers.
3. Only then freeze raw event schema v1 and start the importer.
