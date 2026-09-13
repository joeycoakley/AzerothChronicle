"""Azeroth Chronicle Companion - the always-on background app.

Runs the watcher loop (companion/src/azeroth_chronicle/watcher.py) and shows
a system tray icon: something to see that it's alive, and a normal way to
quit it besides Task Manager. This file is thin on purpose - all the actual
logic (discover, import, recap, publish) lives in the library modules and is
unit tested there. Everything in this file is the one part that cannot be
meaningfully unit tested, because a tray icon needs a real desktop session.

This is also the PyInstaller entry point: `pyinstaller --onefile --noconsole
--name AzerothChronicleCompanion companion/watcher_app.py` produces a
double-clickable .exe that needs no Python install on the machine running
it - see CLAUDE.md for why that is worth doing for this project specifically
(spec section 2.5, "shareable, but not SaaS").

pystray and Pillow are this project's only runtime dependency, and only for
this file. The CLI (`run.py`) and every library module remain pure stdlib;
see CLAUDE.md for why that line is deliberate and where the one exception
was approved.
"""
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from azeroth_chronicle import db as db_module  # noqa: E402
from azeroth_chronicle import watcher  # noqa: E402

APP_NAME = 'Azeroth Chronicle Companion'

# All settings are environment variables rather than a config file or CLI
# flags, since this runs with no console attached once packaged - there is
# nowhere to pass flags to. Names match the pattern llm.py already uses
# (AZEROTH_CHRONICLE_*, OLLAMA_HOST).
POLL_SECONDS = int(os.environ.get('AZEROTH_CHRONICLE_POLL_SECONDS', watcher.DEFAULT_POLL_SECONDS))
GAP_MINUTES = int(os.environ.get('AZEROTH_CHRONICLE_GAP_MINUTES', 120))
WOW_PATH = os.environ.get('AZEROTH_CHRONICLE_WOW_PATH')  # None -> auto-discover
DB_PATH = os.environ.get('AZEROTH_CHRONICLE_DB')          # None -> default location


def _log_dir():
    path = Path(DB_PATH) if DB_PATH else db_module.default_db_path()
    return path.parent


def _setup_logging():
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / 'watcher.log'

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s'))

    root = logging.getLogger('azeroth_chronicle')
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return log_path


def _make_icon_image(color):
    """A plain colored circle, generated in code rather than shipped as a
    binary asset - the color itself is the status signal (watching, quiet,
    or a problem), which is more useful at 16px than any monogram would be.
    """
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 6
    draw.ellipse((margin, margin, size - margin, size - margin), fill=color)
    return image


COLOR_WATCHING = (46, 160, 67, 255)     # green: running normally
COLOR_NO_MODEL = (201, 162, 39, 255)    # amber: local model unreachable
COLOR_ERROR = (200, 60, 60, 255)        # red: unexpected error last pass


def _describe(result):
    if result is None:
        return 'An unexpected error occurred; see watcher.log.', COLOR_ERROR
    if result.recap_errors and not result.new_recaps:
        return ('Waiting for Ollama (local model not reachable).'), COLOR_NO_MODEL
    if result.new_recaps:
        return ('Published %d new recap(s).' % len(result.new_recaps)), COLOR_WATCHING
    if result.imported_events:
        return ('Imported %d new event(s).' % result.imported_events), COLOR_WATCHING
    return 'Watching for new play.', COLOR_WATCHING


def main():
    log_path = _setup_logging()
    log = logging.getLogger('azeroth_chronicle.watcher_app')
    log.info('%s starting. Log file: %s', APP_NAME, log_path)

    try:
        import pystray
    except ImportError:
        print('This app needs pystray and Pillow:\n\n    pip install pystray Pillow\n')
        return 1

    stop_event = threading.Event()
    state = {'last_result': None, 'last_pass_at': None}
    icon_holder = {}

    def on_pass(result):
        state['last_result'] = result
        state['last_pass_at'] = time.time()
        message, color = _describe(result)
        icon = icon_holder.get('icon')
        if icon:
            icon.icon = _make_icon_image(color)
            icon.title = '%s\n%s' % (APP_NAME, message)

    def watcher_thread():
        watcher.run_forever(
            db_path=DB_PATH, wow_path=WOW_PATH, gap_seconds=GAP_MINUTES * 60,
            poll_seconds=POLL_SECONDS, on_pass=on_pass, stop_event=stop_event)

    def recap_now(icon, item):
        # Runs on pystray's own thread; a one-off pass reuses run_once
        # directly against a fresh connection rather than waking the
        # background loop early, so the two never touch the database at
        # the same moment.
        def go():
            conn = db_module.open_database(DB_PATH)
            try:
                result = watcher.run_once(
                    conn, wow_path=WOW_PATH, gap_seconds=GAP_MINUTES * 60)
                on_pass(result)
            finally:
                conn.close()
        threading.Thread(target=go, daemon=True).start()

    def open_log_folder(icon, item):
        try:
            os.startfile(str(_log_dir()))  # Windows-only; this app is Windows-only
        except Exception:
            log.exception('could not open log folder')

    def quit_app(icon, item):
        log.info('quitting')
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem('Recap now', recap_now),
        pystray.MenuItem('Open log folder', open_log_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit', quit_app),
    )

    icon = pystray.Icon(APP_NAME, _make_icon_image(COLOR_WATCHING),
                        title=APP_NAME, menu=menu)
    icon_holder['icon'] = icon

    def setup(icon):
        icon.visible = True
        threading.Thread(target=watcher_thread, daemon=True).start()

    icon.run(setup=setup)
    return 0


if __name__ == '__main__':
    sys.exit(main())
