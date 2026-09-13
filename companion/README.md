# Azeroth Chronicle Companion

Imports the addon's raw event journal into a local SQLite index and answers
questions from it.

Stdlib only. No virtualenv, no pip install, no dependencies to resolve before
the first run. Local-first should mean the tool works on a stock Python.

```text
python companion/run.py status
```

## What exists

Milestone 4 is done: import, materialize, query. No language model yet, which
is Milestone 5.

```text
discover    find SavedVariables files under a WoW installation
import      read SavedVariables into the database, safe to repeat
status      what the database holds, and where it came from
quests      list quests in your history
show <id>   everything captured about one quest
rebuild     discard derived tables and recompute them from raw events
```

Typical first run, pointing at an installation rather than a file:

```text
python companion/run.py import --wow-path "D:\Retail\World of Warcraft"
python companion/run.py quests
python companion/run.py show 456
```

The database defaults to `~/.azeroth-chronicle/chronicle.sqlite3`. Override it
with `--db`. It is kept out of the WoW folder on purpose: it is derived data
and should not be mistaken for part of the game or lost to a reinstall.

## The property that matters

SQLite here is a materialized index, never the source of truth. Three things
follow, and all three are enforced by tests:

- **Importing twice changes nothing.** Events are keyed by the id the addon
  assigns, which is unique by construction, so a reimport inserts zero rows.
- **Deleting the database loses nothing.** Import again and you get a
  byte-identical result, because every derived row is a pure function of the
  events.
- **Derived tables are disposable.** `rebuild` drops and recomputes quests,
  NPCs, dialogue, and item text without touching the raw events.

Materialization deliberately recomputes everything from all events rather than
updating incrementally. That is slower and far harder to get wrong: derived
state cannot drift from the events after a partial or repeated import.

## Layout

```text
run.py                      run without installing
src/azeroth_chronicle/
  luaparse.py               parses the Lua subset WoW writes
  db.py                     connection, migrations, derived-table handling
  importer.py               ingest, then materialize
  discovery.py              locate WoW installations and SavedVariables
  cli.py                    argparse command line
migrations/001_initial.sql  schema
tests/                      unittest, runs against the committed fixture
```

## Tests

```text
python -m unittest discover -s companion/tests
```

They run against `samples/savedvariables/`, not against a live game, so the
suite passes on a machine with no WoW installed.

## Notes on the data

- **NPC identity** comes from the creature id inside the GUID, not the whole
  GUID. The full GUID encodes one particular spawn, so two sightings of the
  same character would otherwise look like two different people.
- **Quest text contains the player's name**, because the client substitutes it.
  Worth remembering when this text reaches a language model.
- **Empty sessions are normal.** Every `/reload` records a session, most with no
  events. They are history, not errors.
- **Missing fields are normal.** The addon writes nothing for a field the client
  did not expose, and `status` reports any session where a required API was
  unavailable, so a gap in the data can be told apart from a capture bug.

## Not built yet

Milestone 5 and the rest of V1: storyline inference, retrieval, the language
model integration, and the "catch me up" recap. Also absent by design are quest
relationships and any external lore. Nothing here reaches the network.
