"""Locate WoW installations and their Azeroth Chronicle SavedVariables.

Kept separate from the importer so that importing never depends on the
game being installed. The importer takes a path; this module is only a
convenience for finding one.

No path is hardcoded (spec section 2.5). Candidates are derived from the
usual install roots on each platform, and the user can always pass
--wow-path instead.
"""
import sys
from pathlib import Path

SAVED_VARIABLES_GLOB = 'WTF/Account/*/*/*/SavedVariables/AzerothChronicle.lua'
ACCOUNT_WIDE_GLOB = 'WTF/Account/*/SavedVariables/AzerothChronicle.lua'

# Flavor directories a single installation may contain.
FLAVORS = ('_classic_era_', '_classic_', '_retail_', '_ptr_', '_beta_')


def candidate_roots():
    """Plausible WoW install roots for this platform, existing ones only."""
    roots = []

    if sys.platform == 'win32':
        bases = [Path('%s:/' % letter) for letter in 'CDEFG']
        for base in bases:
            roots.append(base / 'Program Files (x86)' / 'World of Warcraft')
            roots.append(base / 'Program Files' / 'World of Warcraft')
            roots.append(base / 'World of Warcraft')
            roots.append(base / 'Games' / 'World of Warcraft')
            roots.append(base / 'Retail' / 'World of Warcraft')
    elif sys.platform == 'darwin':
        roots.append(Path('/Applications/World of Warcraft'))
        roots.append(Path.home() / 'Applications' / 'World of Warcraft')
    else:
        home = Path.home()
        roots.append(home / '.wine' / 'drive_c' / 'Program Files (x86)' / 'World of Warcraft')
        roots.append(home / 'Games' / 'World of Warcraft')

    return [r for r in roots if r.is_dir()]


def find_saved_variables(wow_path=None):
    """Every Azeroth Chronicle SavedVariables file under the given roots.

    Returns per-character files and account-wide ones alike. Sorted for
    stable output, since tests and humans both prefer determinism.
    """
    if wow_path:
        roots = [Path(wow_path)]
    else:
        roots = candidate_roots()

    found = []
    for root in roots:
        if not root.is_dir():
            continue

        # A root may be the installation itself or a specific flavor dir.
        search_dirs = [root] + [root / flavor for flavor in FLAVORS]
        for directory in search_dirs:
            if not directory.is_dir():
                continue
            for pattern in (SAVED_VARIABLES_GLOB, ACCOUNT_WIDE_GLOB):
                found.extend(directory.glob(pattern))

    unique = sorted({p.resolve() for p in found})
    return unique


def find_addon_installations(wow_path=None, addon_name='AzerothChronicle'):
    """Every `Interface/AddOns` directory where the named addon is installed.

    Used to publish generated content (recaps) as a sibling addon. Anchoring
    on the main addon's own presence, rather than just any flavor folder that
    exists, means publishing only ever targets an installation the user
    actually set up for this project, and never guesses at one it should not
    touch.
    """
    if wow_path:
        roots = [Path(wow_path)]
    else:
        roots = candidate_roots()

    found = []
    for root in roots:
        if not root.is_dir():
            continue
        search_dirs = [root] + [root / flavor for flavor in FLAVORS]
        for directory in search_dirs:
            addons_dir = directory / 'Interface' / 'AddOns'
            if (addons_dir / addon_name).is_dir():
                found.append(addons_dir)

    return sorted({p.resolve() for p in found})


def describe(path):
    """Pull character and realm out of the SavedVariables path itself.

    The path layout is WTF/Account/<account>/<realm>/<character>/..., so
    this needs no file read. The account segment is deliberately not
    returned: it identifies the player's account and nothing here needs it.
    """
    parts = Path(path).parts
    try:
        idx = len(parts) - 1 - list(reversed(parts)).index('SavedVariables')
    except ValueError:
        return {'realm': None, 'character': None}

    if idx >= 2:
        return {'realm': parts[idx - 2], 'character': parts[idx - 1]}
    return {'realm': None, 'character': None}
