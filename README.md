# Azeroth Chronicle

A personal, spoiler-safe lore journal for World of Warcraft. An addon records the
quests, NPCs, dialogue, books, and world events you actually encounter, and a
local companion turns that raw history into recaps and answers.

The full design lives in [docs/development-spec.md](docs/development-spec.md).

**Current status: capture validated, importer working.** The addon captures a
full quest lifecycle on Classic Era 1.15.9, and the companion imports it into
SQLite. Milestones 0 through 4 pass. No language model yet, which is Milestone 5.

## Repository layout

```text
addon/AzerothChronicle/   The WoW addon (V0 is Core.lua plus the TOC)
companion/                Python companion: import, query, rebuild
docs/                     Development spec and validation checklists
samples/savedvariables/   Sanitized capture used as the companion test fixture
tools/                    Lua lint and the SavedVariables sanitizer
```

## What V0 captures

One append-only event journal in SavedVariables, per character. Nothing is
interpreted, summarized, or deduplicated beyond a short-window gossip filter.

| Source | Events registered |
| --- | --- |
| Quest lifecycle | QUEST_DETAIL, QUEST_ACCEPTED, QUEST_PROGRESS, QUEST_COMPLETE, QUEST_TURNED_IN |
| NPC gossip | GOSSIP_SHOW |
| Books, letters, signs | ITEM_TEXT_READY |
| World dialogue | CHAT_MSG_MONSTER_SAY / YELL / EMOTE / WHISPER |

Each event carries an id of the form `<CharacterGUID>:<SessionID>:<Sequence>`,
a session id, character identity, timestamp, and location.

## Installing for testing

The addon folder must appear inside the client's `Interface/AddOns` directory.
A directory junction beats copying, because edits in this repo take effect on
the next `/reload` with no copy step, and unlike a symlink it needs no
administrator rights.

On this machine the junction is already in place. To recreate it elsewhere,
adjust both paths and run once:

```text
mklink /J "<WoW>\_classic_era_\Interface\AddOns\AzerothChronicle" "<repo>\addon\AzerothChronicle"
```

Remove it later with `rmdir` on the link path, which deletes the link and
leaves the repository untouched. If junctions are not an option, copy
`addon\AzerothChronicle` into `Interface\AddOns\` and re-copy after each edit.

Enable "Azeroth Chronicle" on the character select addon list, and make sure
"Load out of date addons" is checked if the client build has moved past the
interface number in the TOC.

## Interface version

`AzerothChronicle.toc` declares `## Interface: 11509`, read from this machine's
installed client (`wow_classic_era` 1.15.9). When the Forever client is
available, re-read its build and update this number rather than guessing.

## Where your history is written

```text
<WoW>\_classic_era_\WTF\Account\<account>\<realm>\<character>\SavedVariables\AzerothChronicle.lua
```

WoW only writes this file on `/reload`, logout, disconnect, or exit. Nothing
appears on disk mid-session, which is expected and is why the companion imports
at session boundaries rather than streaming.

## The journal pane

`/ac` opens it. Four views: Journey, Quests, Characters, Places. Everything in
it is computed in memory from the events already captured, so it includes the
session in progress, needs no reload, and works with no network and no
language model.

Two rules the pane holds to. Captured text is never replaced by a paraphrase,
because the wording is what you slowed down to read; lists are navigation and
the original is always one click away. And character relevance shows its
evidence rather than a score, so "gave you two quests, met in Shadowglen" is
something you can check.

## In-game commands

```text
/ac                   open the journal pane
/ac status            character, session, event counts
/ac stats             counts broken out by capture type
/ac last              the most recently recorded event
/ac apis              which client APIs resolved, plus live location readout
/ac debug on|off      log each capture as it happens
/ac discovery on|off  log candidate events and which APIs returned data
```

Discovery mode is the tool for validating a new client: it prints what fired and
what each API actually returned, which is how the compatibility layer gets
corrected for Forever.

## Before you reload

There is no Lua interpreter on a typical WoW dev machine, and a broken addon
costs a login cycle to discover. Run the lint first:

```text
python tools\check-lua.py
```

It checks block structure, and flags calls to globals WoW's sandbox does not
expose. That second check exists because `math.randomseed` broke the very first
load: it parses fine and fails at runtime with "attempt to call a nil value".
The lint cannot prove the addon works; `/reload` is still the real test.

## Validation run

Work through [docs/beta-validation-checklist.md](docs/beta-validation-checklist.md).
The short version:

1. Log in, run `/ac status`, confirm a session id appears.
2. Run `/ac discovery on`, then take a quest you have never taken.
3. Turn it in, talk to a few NPCs, read a book, listen for NPC yells.
4. `/reload`, then `/ac stats` to confirm counts survived the write.
5. Open the SavedVariables file and confirm the events reconstruct the interaction.

## Companion

Imports the journal into a local SQLite index. Stdlib only, nothing to install.

```text
python companion/run.py import --wow-path "<your WoW folder>"
python companion/run.py quests
python companion/run.py show 456
```

Importing twice inserts nothing the second time, and deleting the database loses
nothing, because SQLite here is a rebuildable index over the raw journal rather
than the journal itself. See [companion/README.md](companion/README.md).

Run its tests with:

```text
python -m unittest discover -s companion/tests
```

## Privacy

Everything stays on this machine. No telemetry, no cloud, no network calls from
the addon. Do not commit a real `WTF` directory, real account identifiers, or an
unsanitized SavedVariables file; `.gitignore` covers the common cases.
