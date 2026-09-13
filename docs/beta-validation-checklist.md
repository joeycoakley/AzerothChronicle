# Beta Validation Checklist

Derived from development-spec.md section 33, made concrete for an actual play
session. Anything that fails becomes a fix in the API compatibility layer in
`Core.lua`, not a change scattered through capture logic.

Run `/ac discovery on` before starting so each event prints what it found, and
`/ac apis` once per new client to see which APIs resolved at all.

Status below reflects the Classic Era 1.15.9 bring-up run on 2026-09-13:
a night elf hunter in Shadowglen, Teldrassil, taking quest 456 "The Balance
of Nature" and quest 458 "The Woodland Protector".

## Forever beta, day one

Beta opens 2026-09-17; launch is 2026-11-04. Forever is Classic-plus, built
on vanilla, so the Classic Era findings below are a good prior. Do these
first, in order, before playing anything you care about recording.

| Step | Why it comes first |
| --- | --- |
| Read the new client's build and interface number | The TOC number is for Classic Era and will be wrong |
| Add the client's flavor folder to companion discovery | The importer cannot find SavedVariables until it knows the folder name |
| `/ac apis` before anything else | A required API missing here means silent data loss all session |
| `/ac discovery on` for the whole first session | The only record of how Forever differs |
| `/ac stats` and `/reload` after ten minutes | Confirms capture and persistence before a long session |

Capture is the only part that cannot be redone later. A pane, a recap, or an
importer change can all be built against history you already have; a quest
read while the addon was misconfigured is gone.

### New capture paths, unproven on any client

Added ahead of the beta and not yet seen firing. Each has a known trigger.

| Path | How to trigger it |
| --- | --- |
| QUEST_GREETING | Talk to an NPC offering two or more quests at once |
| QUEST_REMOVED, as abandonment | Accept a quest, then abandon it from the log |
| QUEST_REMOVED, as completion | Turn a quest in, and confirm it is not marked abandoned |
| QUEST_LOG_SNAPSHOT | Log in with at least one quest already in the log |
| ZONE_DISCOVERED | Walk into a zone for the first time |
| Race and faction | Check `/ac status` on a Skyborne character |
| PLAYER_LEVEL_UP | Gain a level anywhere |

The quest greeting path is the one most likely to matter immediately. With a
thousand new quests, NPCs offering several at once will be common, and until
now that entire frame was captured as silence.

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
| QUEST_TURNED_IN payload | carries id, xp and money only, no title; join on quest id |
| Quest text personalization | the client substitutes the player name into quest and completion text |
| Single-quest NPCs skip gossip | a quest giver with one available quest opens QUEST_DETAIL directly, with no GOSSIP_SHOW |
| Decorative signposts | named world objects with a mouseover tooltip are not readable and fire nothing |
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
| `/reload` persists events to disk | yes, 11 events across sessions |

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
| QUEST_COMPLETE fires | yes |
| Completion/reward text available (`GetRewardText`) | yes |
| QUEST_TURNED_IN fires | yes |
| XP reward argument present | yes, 170 |
| Money reward argument present | yes, 35 copper |

Repeated QUEST_PROGRESS events are expected and were observed twice with
identical text, because progress dialogue re-fires each time you talk to the
NPC. V0 keeps both. Collapsing them is companion-side work, not addon work.

## NPC and lore

| Check | Observed |
| --- | --- |
| GOSSIP_SHOW fires | yes |
| Gossip text available | yes |
| Which API returned it | `C_GossipInfo`, confirmed by the returned table shape |
| Gossip options available | empty on all four captures so far; see note below |
| Option shape (table of objects, or flat pairs) | not yet observed |
| Active quests list available | yes, structured |
| Available quests list available | empty on all four captures so far; see note below |
| Reopening the same NPC within 30s is deduplicated | not yet exercised |
| ITEM_TEXT_READY fires on a readable book | deferred; the tested signpost was decorative and fired nothing, not even ITEM_TEXT_BEGIN |
| Book text available (`ItemTextGetText`) | not yet exercised |
| Book title available (`ItemTextGetItem`) | not yet exercised |
| Page number available (`ItemTextGetPage`) | not yet exercised |
| Turning a page produces a second event with the next page number | not yet exercised |
| CHAT_MSG_MONSTER_SAY fires | deferred, capture stays live |
| CHAT_MSG_MONSTER_YELL fires | deferred, capture stays live |
| Speaker name available | deferred, capture stays live |
| Speaker GUID present at argument position 12 | deferred, capture stays live |

On the empty gossip options and available quests: this is very likely correct
behavior rather than a capture gap. A quest giver holding a single unaccepted
quest opens the quest frame directly, so GOSSIP_SHOW never fires and there is
nothing for the addon to read. Quest 458 from Melithar Staghelm went straight
to QUEST_DETAIL with no gossip event at all. To populate these fields, the NPC
has to offer a real choice: several quests at once, or a quest alongside
another service such as a vendor, trainer, or flight master.

On readable objects: a named world object with a mouseover tooltip, like the
Starbreeze Village signpost, is decorative. It fires nothing at all, not even
ITEM_TEXT_BEGIN. Genuine readables are sparse in the night elf starting zones.
Quest letters and scrolls in inventory are the most reliable candidates, and
failing those, the bookshelves in larger cities.

## Location

| Check | Observed |
| --- | --- |
| Zone name available (`GetZoneText`) | yes, "Teldrassil" |
| Subzone available (`GetSubZoneText`) | yes, "Shadowglen" |
| Map ID available (`C_Map.GetBestMapForUnit`) | yes, 1438 for Teldrassil |
| Player coordinates available (`C_Map.GetPlayerMapPosition`) | yes, normalized 0-1 (observed 0.587, 0.443) |
| Coordinates nil indoors or in instances? | not yet exercised |
| Position present on real captured events | yes, every event carries mapId, subZone, x, y |

## Milestone acceptance

- [x] Milestone 0: addon loads, SavedVariables initializes, `/ac status` works.
- [x] Milestone 1: quest 456 produced an event with id, quest id, title, full quest text, objectives, NPC name and GUID, timestamp, zone, and session metadata.
- [x] Milestone 2: quest 456 produced all five lifecycle events, including completion text and the XP and money rewards.
- [x] Milestone 3a: gossip captured, with NPC identity and structured active quests.
- [ ] Milestone 3b: readable book or sign captured. Deferred; readable objects are sparse in the night elf starting zones.
- [ ] Milestone 3c: world dialogue captured. Deferred by choice, not blocked.

Milestone 3c is deferred rather than dropped. Capture stays registered for
all four monster chat events, so a say or yell will be recorded passively
whenever one happens near the player. No hunting required; the box gets
ticked when the data shows up. Schema v1 can be frozen without it, since
the dialogue event shape is the simplest of the four capture paths and
carries no fields the other paths have not already exercised.

## Next session

1. Read a book or sign, to close out Milestone 3b.
2. Talk to a quest giver with an unaccepted quest, so gossip options and
   available quests come back populated rather than empty.
3. Step indoors and check whether coordinates go nil, since the companion
   must treat a missing position as normal rather than as corrupt data.

## After the run

1. Copy the SavedVariables file into `samples/savedvariables/` with account
   identifiers and character names stripped or replaced. This becomes the
   fixture the companion importer is built against.
2. Record any API that behaved differently than assumed, and fix it in the
   `AC.API` wrappers.
3. Only then freeze raw event schema v1 and start the importer.
