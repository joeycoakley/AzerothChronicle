"""SQLite connection and migrations.

The database is a materialized index, not the canonical history, so every
operation here is written to be safe to redo. Deleting the file and
importing again must produce the same result, which is the property
Milestone 4 is actually about.
"""
import sqlite3
import time
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / 'migrations'

DEFAULT_DB_NAME = 'chronicle.sqlite3'

# Derived tables, in the order they are safe to clear. These hold nothing
# that cannot be recomputed from the events table, which is what makes
# rebuilding cheap and repeat imports harmless.
DERIVED_TABLES = ('dialogue', 'item_text', 'quests', 'npcs', 'zones')


def default_db_path():
    """Where the database lives when the user does not say.

    Deliberately not next to the WoW installation: this file is derived
    data and should never be mistaken for part of the game, nor end up
    inside a directory the user might sync or reinstall over.
    """
    return Path.home() / '.azeroth-chronicle' / DEFAULT_DB_NAME


def connect(db_path=None):
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    # The importer writes in one transaction per file; WAL keeps a reader
    # (a status query in another terminal) from blocking on it.
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def _applied_versions(conn):
    conn.execute(
        'CREATE TABLE IF NOT EXISTS schema_migrations ('
        ' version TEXT PRIMARY KEY, applied_at INTEGER)'
    )
    rows = conn.execute('SELECT version FROM schema_migrations').fetchall()
    return {row['version'] for row in rows}


def migrate(conn, migrations_dir=None):
    """Apply any migration files not yet recorded. Safe to call every run."""
    directory = Path(migrations_dir) if migrations_dir else MIGRATIONS_DIR
    if not directory.is_dir():
        raise FileNotFoundError('migrations directory not found: %s' % directory)

    applied = _applied_versions(conn)
    ran = []

    for sql_file in sorted(directory.glob('*.sql')):
        version = sql_file.stem
        if version in applied:
            continue
        conn.executescript(sql_file.read_text(encoding='utf-8'))
        conn.execute(
            'INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)',
            (version, int(time.time())),
        )
        ran.append(version)

    conn.commit()
    return ran


def open_database(db_path=None, migrations_dir=None):
    """Connect and bring the schema up to date in one step."""
    conn = connect(db_path)
    migrate(conn, migrations_dir)
    return conn


def clear_derived(conn):
    """Drop everything that can be recomputed from the events table.

    Used before rematerializing. The events, sessions, characters and
    import_state tables survive, since those are the ingested record
    rather than an interpretation of it.
    """
    for table in DERIVED_TABLES:
        conn.execute('DELETE FROM %s' % table)


def table_counts(conn):
    """Row counts for every table, for status output and for tests."""
    names = [row['name'] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {name: conn.execute('SELECT COUNT(*) AS n FROM %s' % name).fetchone()['n']
            for name in names}
