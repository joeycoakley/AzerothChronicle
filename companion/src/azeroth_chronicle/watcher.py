"""Background loop: notice new play, recap it once it's over, publish it.

This is the library half of the always-on companion. It has no GUI and no
tray icon of its own - `watcher_app.py` (the packaged entry point) wires
this into pystray. Kept separate so the actual logic is plain, importable,
and testable without anything resembling a display.

The loop is deliberately simple rather than clever: poll on a timer, import
whatever is on disk (import is already idempotent and cheap - re-parsing an
unchanged file wastes a little CPU, not correctness), then recap and publish
any session that has become closed since the last pass. There is no separate
"has this been recapped" bookkeeping here, because there does not need to be:
recap.generate_and_save_recap already keys on the content hash of the
assembled context, so asking it about an already-recapped session is a cheap
no-op, not a wasted model call.
"""
import logging
import threading
import time

from . import context as context_module
from . import db as db_module
from . import discovery
from . import llm as llm_module
from . import publish as publish_module
from . import recap as recap_module
from .luaparse import LuaParseError

DEFAULT_POLL_SECONDS = 30

log = logging.getLogger('azeroth_chronicle.watcher')


class PassResult:
    """What happened in one pass, for logging and for the tray tooltip."""

    def __init__(self):
        self.imported_events = 0
        self.import_errors = []
        self.new_recaps = []       # list of (window_start, window_end)
        self.recap_errors = []
        self.published_to = []

    @property
    def changed(self):
        return bool(self.new_recaps or self.imported_events)


def run_once(conn, wow_path=None, gap_seconds=context_module.DEFAULT_GAP_SECONDS,
            max_quests=None):
    """One pass: import, recap anything newly closed, publish if anything
    new was generated. Safe to call repeatedly; every step is idempotent.
    """
    result = PassResult()

    for path in discovery.find_saved_variables(wow_path):
        try:
            from . import importer as importer_module
            imported = importer_module.import_file(conn, path)
            result.imported_events += imported.events_inserted
        except (LuaParseError, ValueError, OSError) as exc:
            # A file mid-write, or briefly locked by the client, must not
            # kill the loop - there will be another pass in a few seconds.
            result.import_errors.append('%s: %s' % (path, exc))
            log.warning('import failed for %s: %s', path, exc)

    for session in context_module.find_closed_sessions(conn, gap_seconds):
        try:
            outcome = recap_module.generate_and_save_recap(
                conn, session, max_quests=max_quests,
                on_will_generate=lambda: log.info(
                    'generating recap for session %s-%s', session.start, session.end))
        except llm_module.LlmUnavailable as exc:
            # Local model not running is routine, not exceptional - log it
            # once per pass at a low level and try again next pass, rather
            # than treating it as an error the loop needs to react to.
            result.recap_errors.append(str(exc))
            log.info('recap skipped: %s', exc)
            continue

        if not outcome.cached:
            result.new_recaps.append((session.start, session.end))
            log.info('recap generated for session %s-%s', session.start, session.end)

    if result.new_recaps:
        targets = publish_module.publish(conn, wow_path=wow_path)
        result.published_to = [str(t) for t in targets]
        if targets:
            log.info('published %d new recap(s) to %s',
                     len(result.new_recaps), ', '.join(result.published_to))
        else:
            log.warning('generated %d new recap(s) but found nowhere to publish'
                       ' them - is the main addon installed?', len(result.new_recaps))

    return result


def run_forever(db_path=None, wow_path=None, gap_seconds=context_module.DEFAULT_GAP_SECONDS,
                poll_seconds=DEFAULT_POLL_SECONDS, max_quests=None,
                on_pass=None, stop_event=None):
    """Run `run_once` on a timer until `stop_event` is set.

    `on_pass(result)`, if given, is called after every pass regardless of
    whether anything changed - the caller (the tray app) decides what is
    worth surfacing, this loop does not filter that for them.

    Runs the first pass immediately rather than waiting a full poll
    interval, so starting the app after a play session recaps it right
    away instead of leaving the player wondering if it noticed.
    """
    stop_event = stop_event or threading.Event()
    conn = db_module.open_database(db_path)

    while not stop_event.is_set():
        try:
            result = run_once(conn, wow_path=wow_path, gap_seconds=gap_seconds,
                             max_quests=max_quests)
        except Exception:
            # Nothing from one pass should be able to kill the loop itself;
            # a background app that silently stops watching after one bad
            # pass is worse than one that logs the failure and keeps trying.
            log.exception('unexpected error in watcher pass')
            result = None

        if on_pass and result is not None:
            try:
                on_pass(result)
            except Exception:
                log.exception('on_pass callback raised')

        stop_event.wait(poll_seconds)

    conn.close()
