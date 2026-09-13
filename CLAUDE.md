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
7. **The generated recaps addon is one-way and disposable.** The companion
   writes `AzerothChronicleSummaries` (see `publish.py`) as the only channel
   that gets model output into the game — SavedVariables cannot be used for
   this, because the client owns that file and overwrites it wholesale on
   every reload (spec section 4), discarding anything written there in
   between. Nothing reads this generated addon back; it is rewritten from
   the `summaries` table on every publish, never hand-edited, and its data
   is never treated as an observation or an annotation. A recap only becomes
   visible after the next login or `/reload` — the addon sandbox has no other
   moment where it reads files at all. State that limitation; don't imply
   the pane updates live.

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
- **NULL must survive an update that carries no new information.** A real bug:
  `_upsert_character`'s "level only moves up" SQL used to be
  `MAX(COALESCE(a,0), COALESCE(b,0))`, which looks like it preserves an
  unknown level but actually manufactures a false `0` the moment any
  re-import happens, even one with no level data at all - "never observed"
  silently became "observed as zero". It never showed up against real
  captures (the addon always sends a level), only once the watcher started
  reimporting the same character repeatedly in a test. Fixed with a `CASE`
  that only takes `MAX` once both sides genuinely have a value (see
  `TestCharacterUpsert` in `test_importer.py`). The general lesson: a
  COALESCE-to-a-default inside an aggregate over two nullable columns is a
  common way to accidentally turn "unknown" into "known" - check any new one
  the same way before trusting it.

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
- **Generating Lua from arbitrary text (recap prose) needs a long-bracket
  string, escalated past `[[ ]]` if the text itself contains a closing
  sequence** (see `_lua_long_bracket` in `publish.py`), not quoted-string
  escaping. This also broke the linter itself: its structure checker didn't
  know about `[[ ]]` as a string literal (only as a `--[[` comment), so an
  ordinary English "for" or "end" inside recap prose counted as a real
  keyword and produced a false "unclosed block". Fixed once, in
  `tools/check-lua.py`'s `strip_comments_and_strings`; if you touch that
  function, keep the `prose_with_keywords`-style regression in mind.
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

- **Stdlib only for the CLI and every library module, with exactly one
  documented exception.** `llm.py` talks to a local Ollama server over plain
  HTTP via `urllib.request` - no dependency there. The one exception is
  `watcher_app.py` (the packaged background app), which needs `pystray` +
  `Pillow` for its tray icon and has no other way to show it's alive or offer
  a normal way to quit. That dependency is declared in the `app` extra in
  `pyproject.toml` and used nowhere else - `run.py`, `cli.py`, and every
  module under `src/azeroth_chronicle/` besides the tray glue in
  `watcher_app.py` itself must keep working with nothing installed. If a
  future feature seems to need a new dependency, that is a decision to raise
  explicitly, not to make silently by importing something.
- **PyInstaller is a build-time tool, not a runtime dependency.** It produces
  `AzerothChronicleCompanion.exe` (via `companion/build_exe.py`) and is never
  imported by anything the exe runs. Don't confuse it with the pystray/Pillow
  exception above - installing it does not relax the stdlib-only rule for
  library code, it just builds the one artifact that bundles pystray/Pillow
  so an end user needs no Python at all (spec section 2.5, "shareable, but
  not SaaS").
- **A frozen build's `__file__` does not point where you think.** `db.py`'s
  migrations directory is computed differently depending on whether
  `sys.frozen` is set, because PyInstaller extracts the app to a temp
  directory at runtime and `__file__`-relative climbing lands nowhere real
  there. This cost a real build failure during development
  (`_default_migrations_dir` in `db.py` is the fix, and `build_exe.py`'s
  `--add-data` flag is the other half - both must agree on the bundled
  path). If you add another file the app reads off disk at runtime that
  is not a `.py` module (another data file, a template, anything under
  `migrations/`), it needs the same frozen-aware treatment and an
  `--add-data` entry, or it will work in dev and silently not exist once
  packaged.
- **The recap model runs locally, on purpose, not as a placeholder for a cloud
  model.** The user does not want to pay per token, and a local model fits the
  project's own local-first design better than any hosted one: no key, no
  network call leaving the machine, no bill, ever. Do not "upgrade" this to a
  paid API by default. If a future feature genuinely needs more reasoning than
  a small local model can give, that is a conversation to have explicitly, not
  a silent swap back to a cloud provider.
- **The default model is `qwen2.5:7b-instruct`** (env var
  `AZEROTH_CHRONICLE_MODEL` overrides it), chosen for the project's own
  development hardware (16GB RAM, a 6GB-VRAM GPU). It was verified end to end
  against real captured play: every proper noun in a real recap traced back to
  captured quest text, not the model's training data, but it also blurred two
  separate quests from different NPCs into one paragraph. A small local model
  follows the spoiler-boundary instruction correctly but with less coherence
  than a large one. That tradeoff is accepted and documented, not a bug to fix
  by switching providers.
- **Tests run against the committed fixture**, never a live game, so the suite
  passes on a machine with no WoW.
- **Schema changes are migrations.** Add `companion/migrations/00N_name.sql`.
  They apply in order and are tracked; verify against an existing database, not
  just a fresh one.
- **Never commit an unsanitized capture.** Use
  `tools/sanitize-savedvariables.py`, which refuses to write if identity
  survives. No character names, realms, GUIDs or account ids in the repo.

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

## Before you touch the watcher (`watcher.py`, `watcher_app.py`)

- **`recap.py` is the single place that decides "has this session already
  been recapped."** It answers that by content hash, not by a separate
  tracking table: an identical assembled context means an identical hash,
  which is a cache hit regardless of who's asking. Both the CLI's `recap`
  command and the watcher call `recap.generate_and_save_recap` for exactly
  this reason - a second implementation would risk answering the question
  differently and either double-generating or missing a session.
- **A session is "closed" (safe to recap) when either a later session already
  exists, or enough wall-clock time has passed since its last event.** See
  `context.find_closed_sessions`. The second condition matters as much as the
  first: without it, whichever session is most recent never gets recapped
  until the player does something else entirely, which for someone who plays
  once and logs off is never. Do not recap the open (most recent, still
  growing) session on a mere file-change trigger - that wastes a full local
  generation on a session that will just grow again.
- **One `run_once` pass can and should recap more than one closed session** if
  more than one became eligible since the last pass (e.g. the app was closed
  for a few days). Publish once at the end covering all of them, not once per
  session - publishing is a full rewrite of the generated addon from the
  whole `summaries` table, so doing it per-session is wasted, identical work.
- **`run_once` must not raise.** A bad SavedVariables parse, a local model
  that isn't running, an unexpected exception - none of it should be able to
  stop `run_forever`'s loop. A background app that silently stops watching
  after one bad pass is worse than one that logs the failure and keeps
  polling. `PassResult` exists so a caller (the tray app, or a test) can see
  what happened without the pass needing to throw to report it.
- **I cannot test the tray icon, the menu, or any actual click.** Those need
  a real desktop session. What was verified: the packaged `.exe` launches,
  runs a real pass against the real WoW installation and a real local model,
  writes correct log output, and the process was killed by PID rather than
  by clicking Quit - the graceful `stop_event.set(); icon.stop()` path in
  `quit_app` is plausible-looking code, not something I confirmed executes
  correctly. Say so; don't imply more than that was checked.

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

The recap/watcher/packaged-app work (`recap.py`, `watcher.py`, `watcher_app.py`,
`publish.py`) was built ahead of the beta as an explicit, user-directed
exception to "capture completeness beats features," not a signal that the
priority order changed generally. Treat further feature requests the same
way: build them if asked, but capture validation is still what actually needs
the remaining time before Thursday.

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
- Reintroduce a paid or hosted LLM API as the recap's default. The user
  explicitly does not want to pay per token; the local Ollama path was chosen
  for that reason and fits the project's local-first design besides. A cloud
  provider is a fine thing to support as an opt-in choice someone configures,
  never the thing that runs when nobody set anything up.
- Claim something was verified in-game. You cannot run the client.
