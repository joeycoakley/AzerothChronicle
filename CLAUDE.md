# Working on Azeroth Chronicle

Read this before changing anything. It encodes decisions already made and
mistakes already paid for. If you want to deviate, say so and why; don't
quietly do it differently.

## The one-paragraph model

A WoW addon records what the player actually encountered into an append-only
journal in SavedVariables. A Python companion imports that into SQLite and
answers questions from it. The journal is the truth; everything else is a
rebuildable view of it. The product promise is that nothing is ever reported
that the player did not personally encounter, because the player is reading a
new game's story slowly and an invented detail spoils it.

## Invariants

Breaking one of these is a design change, not a refactor.

1. **Events are append-only and never edited.** They are observations: what the
   client said happened. Threads and notes are annotations: what the player
   decided it meant. Annotations live outside `events` (see
   `AzerothChronicleDB.threads`). Do not mix them.
2. **Everything except `events` is derived and disposable.** Deleting the SQLite
   file and reimporting must reproduce it exactly. Tests enforce this.
3. **Import is idempotent.** Events are keyed by the addon's event id. Never
   synthesize an id for an event missing one: a fabricated key duplicates on the
   next import. Skip and report instead.
4. **Materialization recomputes from all events**, not incrementally. Slower and
   far harder to get wrong. Keep it that way.
5. **Capture is the only unrecoverable part.** A UI or importer bug can be fixed
   later against history you already have. A quest read while the addon was
   misconfigured is gone. Prioritize accordingly.
6. **No external lore, ever, without an explicit opt-in that does not yet
   exist.** `companion/src/azeroth_chronicle/context.py` is the spoiler
   boundary. Anything reaching a model goes through it.

## Honesty rules

The project's value is that it only reports what was observed. These are not
style preferences.

- **Do not infer and present as fact.** Prefer showing evidence: "gave you two
  quests, met in Shadowglen" over a relevance score. Wrong groupings and false
  confidence are worse than no feature.
- **NULL when ambiguous.** The abandonment logic is the model case: the client
  fires one event for abandoning and for handing in, so removal is recorded raw
  and judged only after every event for that quest is seen. See
  `_resolve_abandonment` in `importer.py`.
- **Never claim completion you cannot verify.** Threads are "quiet", never
  "finished", because we cannot know whether a story has more chapters.
- **Never truncate silently.** Say what was left out.
- **Distinguish "not observed" from "did not happen."** `known_from_snapshot`
  and `known_complete` exist for exactly this.

## Before you write addon Lua

The sandbox is not standard Lua and the client hides errors. Every item below
cost a debugging round.

- **Run the linter before asking the user to reload.** It catches the three
  classes that fail invisibly:
  ```
  python tools/check-lua.py
  ```
- **Never call a client API directly.** Add a wrapper in `AC.API` in `Core.lua`,
  try the modern call, fall back to the legacy global, return nil rather than
  throwing. Then add it to `API_PROBES` so `/ac apis` reports it.
- **Verify API names empirically before using them.** Grep the user's installed
  addons; they are a corpus of what actually works on this client:
  ```
  grep -rl "C_Map.GetBestMapForUnit" "<wow>/Interface/AddOns"
  ```
  `C_Map.GetBestMapID` does not exist. It was guarded by an existence check, so
  it failed silently and every event lost its coordinates for a whole session.
- **A guarded call to a nonexistent API is invisible.** That is why probes and
  the `missingApis` session field exist. Prefer a visible gap to missing data.
- **Forward references resolve to nil globals.** A local called above its
  declaration is not an error in Lua; it fails at runtime, silently. Forward
  declare at the top of the file. The linter checks this.
- **`math.randomseed` is not exposed.** Nor `os.*`, `io.*`, `require`,
  `loadstring`. The linter has the list.
- **WoW hides Lua errors by default.** Wrap handlers and report through
  `AC.Debug.Error` so a failure says something instead of looking like a no-op.
- **Every capture path is wrapped in `pcall`.** Capture must never break quest
  interaction.
- **Argument signatures differ across client branches.** Store raw args
  alongside your interpretation (see `QUEST_ACCEPTED`, `PLAYER_LEVEL_UP`), so a
  changed signature is detectable rather than silently misread.
- **Frame templates are chosen with a fallback.** A missing template would stop
  the addon loading entirely.

## Before you write companion Python

- **Stdlib only, except the recap.** `anthropic` is the single optional
  dependency, imported lazily. Import, status, quests, show and rebuild must run
  on a stock Python with nothing installed. Do not add dependencies.
- **Tests run against the committed fixture**, never a live game, so the suite
  passes on a machine with no WoW.
- **Schema changes are migrations.** Add `companion/migrations/00N_name.sql`.
  They apply in order and are tracked; verify against an existing database, not
  just a fresh one.
- **Never commit an unsanitized capture.** Use
  `tools/sanitize-savedvariables.py`, which refuses to write if identity
  survives. No character names, realms, GUIDs or account ids in the repo.
- **The recap model is `claude-sonnet-5`, deliberately, in `llm.py`.** A recap is
  prose over a small, already-structured context (see `context.py`), not
  multi-step reasoning, so it doesn't need Opus's headroom, and Sonnet costs
  under half as much per token. Don't "upgrade" this to Opus by default; if a
  future feature genuinely needs more reasoning, decide per-feature, not by
  bumping the whole file.
- **No server-side refusal fallback is wired.** The documented `fallbacks:
  "default"` form is only demonstrated with `claude-opus-5` as the requesting
  model, and recap content (fantasy quest text) carries essentially no
  policy-refusal risk, so it isn't worth wiring against an unconfirmed model
  pairing. A refusal, if it ever happens, surfaces as `LlmRefused` instead of
  being silently retried.

## Commands

```
python tools/check-lua.py                         lint the addon
python -m unittest discover -s companion/tests    run tests
python companion/run.py import --wow-path "..."   import a capture
python companion/run.py recap --dry-run           see what a model would get
```

## Testing

Write a test when the behaviour is a judgment call that could silently be wrong:
idempotency, abandonment, ordering independence, spoiler leakage. Existing tests
encode decisions, so if one fails, understand the decision before changing it.
Do not test the Lua; there is no interpreter here. The linter plus an in-game
reload is the verification path, and you cannot verify rendering yourself. Say
so rather than implying you tested it.

## Style

- **Comments explain why, not what.** Especially for a non-obvious decision or a
  trap. "The client fires the same event for both, so the judgment waits" earns
  its place; "loop over quests" does not.
- Match surrounding code. No emoji. No decorative output.
- Commit messages: what changed and why it matters, in prose. Explain the
  reasoning behind a non-obvious choice, because that is what a future reader
  needs. Look at `git log` before writing one.

## Current priorities

WoW Forever beta opens 2026-09-17, launch 2026-11-04. It is Classic-plus, built
on vanilla, with roughly a thousand new quests and no quest database in
existence. Until the beta client is in hand:

- Capture completeness beats features.
- Two things are known-pending and need the real client: the interface number in
  the addon manifest is Classic Era's, and companion discovery does not know
  Forever's flavour folder name.
- `docs/beta-validation-checklist.md` is the day-one procedure and the record of
  what has been confirmed. Update it when you learn something about the client.

## Do not

- Refactor `Core.lua` into modules before the beta. Capture is validated; leave
  it alone and add new files instead.
- Add automatic storyline inference. It was considered and rejected: prerequisite
  data does not exist client-side, heuristics were inconsistent, and a wrong
  grouping is exactly the confident falsehood this project avoids. Threads are
  hand-made; the addon only suggests from things it observed.
- Replace captured text with a paraphrase in the UI. Lists are navigation; the
  original wording is always one click away.
- Put an API key anywhere near the addon.
- Claim something was verified in-game. You cannot run the client.
