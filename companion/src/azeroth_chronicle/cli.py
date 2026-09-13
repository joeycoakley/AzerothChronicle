"""Command line interface for the Azeroth Chronicle companion.

argparse rather than a framework, so the companion runs against a stock
Python with nothing installed. Local-first means the tool should work on a
machine where the user has not been asked to set anything up.
"""
import argparse
import sys
from pathlib import Path

from . import db as db_module
from . import discovery
from . import importer
from .luaparse import LuaParseError


def _open(args):
    return db_module.open_database(args.db)


def cmd_discover(args):
    paths = discovery.find_saved_variables(args.wow_path)
    if not paths:
        print('No Azeroth Chronicle SavedVariables found.')
        print('Pass --wow-path pointing at your World of Warcraft folder.')
        return 1

    for path in paths:
        info = discovery.describe(path)
        label = ' - '.join(p for p in (info['character'], info['realm']) if p)
        print('%s%s' % (path, ('  [%s]' % label) if label else ''))
    return 0


def cmd_import(args):
    paths = [Path(p) for p in args.paths] if args.paths else discovery.find_saved_variables(args.wow_path)

    if not paths:
        print('Nothing to import. Pass a file path, or --wow-path to search.')
        return 1

    conn = _open(args)
    failures = 0

    for path in paths:
        if not Path(path).is_file():
            print('skip  %s (not a file)' % path)
            failures += 1
            continue
        try:
            result = importer.import_file(conn, path)
        except (LuaParseError, ValueError) as exc:
            # One unreadable file must not abort the rest of the import.
            print('FAIL  %s' % path)
            print('      %s' % exc)
            failures += 1
            continue

        print('ok    %s' % path)
        print('      %d events seen, %d new, %d already present'
              % (result.events_seen, result.events_inserted, result.events_skipped))
        if result.malformed:
            print('      %d malformed events skipped:' % len(result.malformed))
            for note in result.malformed[:5]:
                print('        %s' % note)

    counts = db_module.table_counts(conn)
    print('\ndatabase now holds %d events, %d quests, %d NPCs, %d dialogue lines'
          % (counts.get('events', 0), counts.get('quests', 0),
             counts.get('npcs', 0), counts.get('dialogue', 0)))
    return 1 if failures else 0


def cmd_status(args):
    conn = _open(args)
    counts = db_module.table_counts(conn)

    print('database: %s' % (args.db or db_module.default_db_path()))
    print()
    for name in ('events', 'characters', 'sessions', 'quests', 'npcs', 'dialogue', 'item_text'):
        print('  %-12s %d' % (name, counts.get(name, 0)))

    characters = conn.execute(
        'SELECT name, realm, class, level_last_seen FROM characters ORDER BY name'
    ).fetchall()
    if characters:
        print('\ncharacters:')
        for row in characters:
            print('  %s - %s (%s, level %s)'
                  % (row['name'], row['realm'], row['class'], row['level_last_seen']))

    imports = conn.execute(
        'SELECT source_path, events_seen, events_inserted FROM import_state'
    ).fetchall()
    if imports:
        print('\nimported from:')
        for row in imports:
            print('  %s' % row['source_path'])
            print('    %d events seen, %d new on last run'
                  % (row['events_seen'], row['events_inserted']))

    # Surface any session where the client could not provide a required
    # API, since that explains gaps in the data that otherwise look like
    # capture bugs.
    gaps = conn.execute(
        'SELECT session_id, missing_apis_json FROM sessions'
        ' WHERE missing_apis_json IS NOT NULL'
    ).fetchall()
    if gaps:
        print('\nsessions with missing client APIs:')
        for row in gaps:
            print('  %s: %s' % (row['session_id'], row['missing_apis_json']))

    return 0


def cmd_quests(args):
    conn = _open(args)
    rows = conn.execute(
        'SELECT q.quest_id, q.title, q.accepted_at, q.turned_in_at,'
        '       q.xp_reward, q.money_reward, n.display_name AS giver'
        '  FROM quests q LEFT JOIN npcs n ON n.npc_key = q.giver_npc_key'
        ' ORDER BY COALESCE(q.first_seen_at, 0)'
    ).fetchall()

    if not rows:
        print('No quests recorded yet.')
        return 0

    for row in rows:
        if row['turned_in_at']:
            state = 'turned in'
        elif row['accepted_at']:
            state = 'accepted'
        else:
            state = 'seen'
        reward = ''
        if row['xp_reward'] or row['money_reward']:
            reward = '  (%s xp, %s copper)' % (row['xp_reward'] or 0, row['money_reward'] or 0)
        print('[%s] %s' % (row['quest_id'], row['title'] or '(untitled)'))
        print('    %s%s' % (state, reward))
        if row['giver']:
            print('    from %s' % row['giver'])
    return 0


def cmd_show(args):
    """Everything captured about one quest, which is what a recap reads."""
    conn = _open(args)
    quest = conn.execute(
        'SELECT * FROM quests WHERE quest_id = ?', (args.quest_id,)).fetchone()

    if not quest:
        print('Quest %s is not in your history.' % args.quest_id)
        return 1

    print('[%s] %s' % (quest['quest_id'], quest['title'] or '(untitled)'))

    giver = None
    if quest['giver_npc_key']:
        giver = conn.execute('SELECT display_name FROM npcs WHERE npc_key = ?',
                             (quest['giver_npc_key'],)).fetchone()
    if giver:
        print('from %s' % giver['display_name'])

    for label, field in (('description', 'description'),
                         ('objectives', 'objectives_text'),
                         ('on completion', 'completion_text')):
        if quest[field]:
            print('\n%s:\n%s' % (label, quest[field]))

    lines = conn.execute(
        'SELECT dialogue_type, text FROM dialogue WHERE quest_id = ? ORDER BY timestamp',
        (args.quest_id,)).fetchall()
    if lines:
        print('\nwhat you were told:')
        for line in lines:
            print('  (%s) %s' % (line['dialogue_type'], line['text']))
    return 0


def _format_window(start, end):
    import datetime
    fmt = '%d %b %H:%M'
    return '%s to %s' % (datetime.datetime.fromtimestamp(start).strftime(fmt),
                         datetime.datetime.fromtimestamp(end).strftime(fmt))


def cmd_sessions(args):
    """Stretches of play, which is not the same as the addon's sessions."""
    from . import context as context_module

    conn = _open(args)
    sessions = context_module.find_play_sessions(conn, args.gap * 60)

    if not sessions:
        print('No recorded activity yet.')
        return 0

    for number, session in enumerate(reversed(sessions), 1):
        print('%2d. %s   %d events'
              % (number, _format_window(session.start, session.end),
                 session.event_count))
    print('\nThe most recent is 1. Recap one with: recap --session N')
    return 0


def cmd_recap(args):
    from . import context as context_module

    conn = _open(args)
    sessions = context_module.find_play_sessions(conn, args.gap * 60)

    if not sessions:
        print('No recorded activity to recap yet. Import a capture first.')
        return 1

    ordered = list(reversed(sessions))
    if args.session < 1 or args.session > len(ordered):
        print('There are %d recorded play sessions; asked for %d.'
              % (len(ordered), args.session))
        return 1

    session = ordered[args.session - 1]

    if args.dry_run:
        # The whole point of the spoiler promise is that it can be checked.
        text, event_ids, stats = context_module.build_recap_context(
            conn, session, max_quests=args.max_quests)
        print('Play session: %s' % _format_window(session.start, session.end))
        print('Drawn from %d events: %d quests, %d characters.'
              % (stats['events'], stats['quests'], stats['npcs']))
        print()
        print('--- exactly what would be sent, and nothing else ---')
        print(text)
        print('--- end ---')
        print('\nNothing was sent. Drop --dry-run to generate the recap.')
        return 0

    from . import llm
    from . import recap as recap_module

    print('Play session: %s' % _format_window(session.start, session.end))

    def announce_generation():
        print('Generating locally with %s. This can take a minute or two'
              ' on modest hardware; nothing is sent over the network.' % llm.MODEL)

    try:
        outcome = recap_module.generate_and_save_recap(
            conn, session, max_quests=args.max_quests, force=args.force,
            save=not args.no_save, on_will_generate=announce_generation)
    except llm.LlmUnavailable as exc:
        print(exc)
        return 1

    print('Drawn from %d events: %d quests, %d characters.\n'
          % (outcome.stats['events'], outcome.stats['quests'], outcome.stats['npcs']))
    print(outcome.text)

    if outcome.cached:
        print('\n(unchanged since the last recap, so it was not regenerated;'
              ' use --force to redo it)')
    elif outcome.input_tokens:
        print('\n(%s, %s tokens in, %s out)'
              % (outcome.model, outcome.input_tokens, outcome.output_tokens))

    if not args.no_save and not outcome.cached and not args.no_publish:
        _publish_to_game(conn, args)

    return 0


def _publish_to_game(conn, args):
    """Shared by `recap` (auto-publish) and the standalone `publish` command."""
    from . import publish as publish_module

    targets = publish_module.publish(conn, wow_path=getattr(args, 'wow_path', None))
    if targets:
        print('\nPublished to the game. Log in or /reload to see it in the'
              ' journal pane, under Recaps.')
        for target in targets:
            print('  %s' % (target / publish_module.ADDON_NAME))
    else:
        print('\nCould not publish: no WoW installation with the'
              ' AzerothChronicle addon was found.'
              ' Pass --wow-path if it is somewhere nonstandard.')
    return targets


def cmd_publish(args):
    """Stand-alone republish, for when the addon file was lost or the game
    moved, without regenerating (and re-spending time on) every recap."""
    conn = _open(args)
    targets = _publish_to_game(conn, args)
    return 0 if targets else 1


def cmd_rebuild(args):
    """Prove the derived tables are disposable by discarding and redoing them."""
    conn = _open(args)
    before = db_module.table_counts(conn)
    importer.materialize(conn)
    conn.commit()
    after = db_module.table_counts(conn)

    print('rebuilt derived tables from %d events' % after.get('events', 0))
    for name in db_module.DERIVED_TABLES:
        print('  %-10s %d -> %d' % (name, before.get(name, 0), after.get(name, 0)))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='azeroth-chronicle',
        description='Import and query your personal Azeroth Chronicle history.')
    parser.add_argument('--db', help='database path (default: ~/.azeroth-chronicle/chronicle.sqlite3)')

    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('discover', help='find SavedVariables files')
    p.add_argument('--wow-path', help='World of Warcraft installation folder')
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser('import', help='import SavedVariables into the database')
    p.add_argument('paths', nargs='*', help='files to import (default: search for them)')
    p.add_argument('--wow-path', help='World of Warcraft installation folder')
    p.set_defaults(func=cmd_import)

    p = sub.add_parser('status', help='what the database currently holds')
    p.set_defaults(func=cmd_status)

    p = sub.add_parser('quests', help='list quests in your history')
    p.set_defaults(func=cmd_quests)

    p = sub.add_parser('show', help='everything captured about one quest')
    p.add_argument('quest_id', type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser('sessions', help='stretches of play the Chronicle recorded')
    p.add_argument('--gap', type=int, default=120,
                   help='minutes of inactivity that end a session (default 120)')
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser('recap', help='summarize a stretch of play')
    p.add_argument('--session', type=int, default=1,
                   help='which play session, 1 being the most recent')
    p.add_argument('--gap', type=int, default=120,
                   help='minutes of inactivity that end a session (default 120)')
    p.add_argument('--dry-run', action='store_true',
                   help='print exactly what would be sent, and send nothing')
    p.add_argument('--force', action='store_true',
                   help='regenerate even if an identical recap already exists')
    p.add_argument('--no-save', action='store_true',
                   help='do not store the result')
    p.add_argument('--no-publish', action='store_true',
                   help='do not write it into the game as a generated addon')
    p.add_argument('--wow-path', help='World of Warcraft installation folder')
    p.add_argument('--max-quests', type=int, default=None,
                   help='cap how many quests go into the context')
    p.set_defaults(func=cmd_recap)

    p = sub.add_parser('publish',
                       help='write stored recaps into the game as a generated addon')
    p.add_argument('--wow-path', help='World of Warcraft installation folder')
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser('rebuild', help='rebuild derived tables from raw events')
    p.set_defaults(func=cmd_rebuild)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, 'func', None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
