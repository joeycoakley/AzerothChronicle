#!/usr/bin/env python3
"""Build AzerothChronicleCompanion.exe with PyInstaller.

Run from anywhere:  python companion/build_exe.py

A plain PyInstaller command line is easy to get subtly wrong twice over on
Windows: relative --add-data sources resolve against --specpath rather than
the current directory (silently doubling the path), and a shell can mangle
a hand-typed Windows path before PyInstaller ever sees it. Both bit this
build during development. Building the command from Path objects in Python
sidesteps both classes of mistake instead of documenting a fragile
one-liner someone has to retype correctly.

Needs `pip install pyinstaller pystray Pillow` first - these are dev/runtime
tools for producing and running the packaged app, never dependencies of the
CLI or the importer, which stay pure stdlib.
"""
import shutil
import subprocess
import sys
from pathlib import Path

COMPANION_DIR = Path(__file__).resolve().parent
REPO_ROOT = COMPANION_DIR.parent


def main():
    dist_dir = COMPANION_DIR / 'dist'
    build_dir = COMPANION_DIR / 'build'

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--noconsole',                     # no console window for a tray app
        '--name', 'AzerothChronicleCompanion',
        '--paths', str(COMPANION_DIR / 'src'),
        # migrations/*.sql are data, never analyzed as Python imports, so
        # PyInstaller only knows to include them because we say so here -
        # and db.py's _default_migrations_dir() must agree on where they
        # land inside the bundle (sys._MEIPASS / 'migrations').
        '--add-data', '%s;migrations' % (COMPANION_DIR / 'migrations'),
        '--distpath', str(dist_dir),
        '--workpath', str(build_dir),
        '--specpath', str(COMPANION_DIR),
        str(COMPANION_DIR / 'watcher_app.py'),
    ]

    print('Running:', ' '.join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        return result.returncode

    exe_path = dist_dir / 'AzerothChronicleCompanion.exe'
    print('\nBuilt: %s (%.1f MB)' % (exe_path, exe_path.stat().st_size / 1_000_000))
    print('This does not verify the exe runs - launch it once and check the log')
    print('to confirm a real pass completed:')

    sys.path.insert(0, str(COMPANION_DIR / 'src'))
    from azeroth_chronicle import db as db_module  # noqa: E402
    print('  %s' % (db_module.default_db_path().parent / 'watcher.log'))

    # Leftover build/ intermediates are large and reproducible; no reason to
    # make the next build think anything changed just because they exist.
    shutil.rmtree(build_dir, ignore_errors=True)

    return 0


if __name__ == '__main__':
    sys.exit(main())
