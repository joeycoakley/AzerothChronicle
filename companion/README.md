# Azeroth Chronicle Companion

Not yet implemented. This is V1 work and is deliberately deferred until the
addon's raw event schema is validated against a real client and frozen
(development-spec.md sections 32 and 38).

Planned stack: Python, SQLite, dataclasses or Pydantic, Typer CLI.

Planned first commands:

```text
azeroth-chronicle import
azeroth-chronicle status
azeroth-chronicle quests
azeroth-chronicle recap
```

The importer is built against a sanitized capture placed in
`samples/savedvariables/`, not against a live WoW installation, so tests do not
require the game to be installed.

Directories:

```text
src/         package source
tests/       importer and inference tests
migrations/  SQLite schema migrations
```

SQLite here is a materialized index, never the source of truth. It must be
deletable and fully rebuildable from the addon's raw event journal.
