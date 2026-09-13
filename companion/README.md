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

## Recaps

Runs against a local model through [Ollama](https://ollama.com), reached over
plain HTTP with the standard library. No pip package, no API key, no per-token
cost, and no game text ever leaves the machine, not even to a cloud provider.

```text
winget install Ollama.Ollama
ollama pull qwen2.5:7b-instruct

python companion/run.py sessions
python companion/run.py recap --dry-run
python companion/run.py recap
```

A play session here is a stretch of activity, not one of the addon's sessions.
The addon records a session per reload, so an evening produces a dozen; they are
regrouped by gaps in the timeline, two hours by default, adjustable with `--gap`.

**Read `--dry-run` output before you trust the feature.** It prints the exact
text that would be sent and sends nothing. The spoiler promise is that the model
only ever sees what the character encountered, and a privacy claim you cannot
inspect is worth very little. Two things enforce it: retrieval decides what can
be known, and the system prompt forbids filling gaps from the model's own
knowledge of Warcraft. Both are needed, and both were checked against a real
recap during development: every proper noun the model used traced back to
captured quest text, not the model's own training.

That check also surfaced the honest tradeoff of a small local model: it
correctly used only captured text, but blurred two separate quests from
different NPCs into one paragraph where a larger model would likely have kept
them apart. Free and private costs some coherence on modest hardware. Read the
output, don't just trust it.

Generation is slow relative to a cloud API: a few minutes rather than a few
seconds on a modest GPU, since a 7B model only partially offloads on 6GB of
VRAM. That is the cost of nothing ever leaving the machine.

Every recap stores the ids of the events it was built from, so a summary can be
audited against its sources and regenerated later with a better model. Identical
input is not regenerated unless you pass `--force`.

The model is configurable without touching code: `AZEROTH_CHRONICLE_MODEL` picks
a different Ollama tag, `OLLAMA_HOST` points at a different server. Without
Ollama running, recaps explain what is missing and everything else keeps
working.

## Not built yet

Export/import for moving history between machines, and a way to feed threads
(the hand-grouped story annotations) into a recap's context alongside the raw
quest data. Also absent by design: any external lore, and any network call
other than to localhost.
