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

## Getting a recap into the game

The addon sandbox cannot make network calls or read arbitrary files, so the
companion cannot hand a recap to the addon directly. SavedVariables looks like
an obvious bridge and is not: the client owns that file and rewrites it
wholesale from memory on every reload, so anything the companion wrote there
would just be overwritten (spec section 4).

The bridge that actually works is a second addon whose Lua *is* the data.
`recap` writes one automatically, called `AzerothChronicleSummaries`, right
alongside the main addon:

```text
python companion/run.py publish              # (re)write it without regenerating
python companion/run.py recap --no-publish   # generate only, skip this step
```

It is found by locating wherever `AzerothChronicle` itself is installed, never
by guessing at a WoW folder, and it is rewritten from the `summaries` table
every time, so it is exactly as disposable as every other derived thing in
this project. Enable "Azeroth Chronicle Summaries" once on the character-select
addon list, like any other addon.

**The real limitation, stated rather than hidden: a recap only appears after
your next login or `/reload`, never mid-session.** That is the only time the
client reads addon files at all. Playing, then generating a recap, then
opening the pane in the same session shows nothing new until you reload;
that reload is also what makes the recap visible, so it costs you nothing
beyond doing it once.

Generated Lua is written from arbitrary model text, so it wraps every recap in
a Lua long-bracket string sized to whatever the text actually contains (widening
past `[[ ]]` if the text itself contains a closing sequence) rather than assuming
short-string escaping is enough. Tested against exactly that: quotes, backslashes,
and embedded `]]` sequences all round-trip correctly.

## The chronicle: your character's whole story, one chapter per place

A session recap answers "what did I just do." The chronicle answers a
different question: "what has my character's whole story been so far, from
the beginning." It writes one chapter per zone you have visited, in the order
you first arrived, using the exact same generated-addon bridge described
above (`kind = 'zone_chapter'` in the same `summaries` table as recaps).

```text
python companion/run.py chapters                      # list zones and their status
python companion/run.py chronicle --zone "Teldrassil" --dry-run
python companion/run.py chronicle                      # write or update every zone
```

A chapter is a genuinely different kind of row from a session recap, not just
a longer one. A session is closed and immutable, so two different sessions
always get two different rows. A zone is the opposite - you will very likely
go back and do more there weeks later - so a chapter has **one stable row per
zone that updates in place** as more happens there, rather than a growing
pile of "Teldrassil" entries. Re-running `chronicle` after more play is
expected and cheap: a zone with nothing new is a cache hit, and only zones
that actually changed spend model time. `--force` regenerates regardless.

Chapters draw from every quest and conversation tied to that zone across the
character's whole history, not one play session's worth, so the context is
larger and takes longer: budget several minutes per zone, not one. `--zone`
updates a single zone if you don't want to wait through every one.

**Read the output. This is the part of the project where a small local model
shows its limits most plainly.** During development, real generated chapters:

- used Markdown headers despite an explicit instruction not to - fixed with a
  defensive strip applied to everything the model returns (`_strip_markdown`
  in `llm.py`), since relying on the instruction alone was not enough;
- persistently closed with a generic "ready for whatever comes next"
  flourish despite two different rewrites of the instruction against it -
  this is stylistic noise, not an actual spoiler (it never names anything
  specific), and was left as a known, accepted limitation rather than chased
  further;
- once described an NPC with a detail invented rather than drawn from
  captured text ("a young druid apprentice," unsupported by anything in her
  captured dialogue). This is the one that matters most, since it is a real
  instance of the exact failure this whole project exists to prevent, not
  just a style tic.

None of this makes the chronicle useless - the factual backbone (which
quests, which NPCs, what was said, in what order) was accurate throughout.
It means what recaps already meant: this is prose written by a small model
running for free on modest hardware, worth reading with that in mind, not
worth mistaking for a definitive account.

## Running it automatically: the background app

`recap` and `publish` above are commands you run by hand after a session. For
running unattended - like WarcraftLogs' uploader client - there's a background
app that watches for new play and recaps it on its own, with no command to
remember:

```text
pip install pystray Pillow          # only for this app; the CLI needs neither
python companion/watcher_app.py
```

It polls for SavedVariables changes, imports, and once a play session is
**closed** (not the one still growing - either a later session already exists,
or enough time has passed that you've plausibly logged off for the day),
recaps and publishes it automatically. A system tray icon shows it's alive
(green while watching, amber if Ollama isn't reachable, red on an unexpected
error) with a right-click menu for "Recap now" and "Quit". Logs go to
`~/.azeroth-chronicle/watcher.log`.

Note that the tray menu's "Recap now" still respects the closed-session rule
- it forces an early check, not an early close, so clicking it moments after
turning in a quest finds nothing new yet. For that, use the in-game button
below instead.

### Getting a recap without waiting

Waiting two hours (or logging off for the night) is the normal way a session
closes, but the journal pane also has a "Request recap" button - on the
Journey view and on the Recaps view - for "I just did some things, summarize
this now." Clicking it writes one timestamp into SavedVariables and reloads
the UI immediately, which is what actually lets the companion see the
request without you remembering to `/reload` by hand.

That request overrides the wall-clock rule for the session you were just in,
nothing else. The recap itself still runs in the background at its usual
pace, and still needs one further login or `/reload` to actually appear
under Recaps - a button in a sandboxed addon can shorten the wait, not
collapse the whole round trip into one click.

To build it as a standalone `.exe` that needs no Python installed at all
(handy for giving this to a friend, per the spec's "shareable, but not SaaS"
principle):

```text
pip install pyinstaller
python companion/build_exe.py
```

Produces `companion/dist/AzerothChronicleCompanion.exe`. Verified during
development against the real WoW installation and a real local model: it
launches, discovers the game, imports, generates a genuine recap, and
publishes it, all without a console window. What was not verified is
anything requiring an actual click on the tray icon - that needs a real
desktop session, which automated testing here does not have.

`pystray`, `Pillow`, and `pyinstaller` are the one deliberate exception to
this project's stdlib-only rule, used only by this app and its build step;
`run.py` and every library module still need nothing installed. See
`CLAUDE.md` if you're touching any of this.

## Not built yet

Export/import for moving history between machines, and a way to feed threads
(the hand-grouped story annotations) into a recap's context alongside the raw
quest data. Also absent by design: any external lore, and any network call
other than to localhost.
