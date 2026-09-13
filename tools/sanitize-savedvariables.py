"""Turn a real capture into a shareable test fixture.

The addon's SavedVariables file is personal history: it carries the
player's character name, realm, and character GUID, and the client also
substitutes the character name directly into quest and completion text.
None of that belongs in a repository, but the captured game content is
exactly what the companion importer needs to be built against.

This replaces player identity with stable placeholders and leaves game
content alone. NPC names and creature GUIDs are kept deliberately: they
are game data, not player data, and the importer's NPC identity handling
cannot be tested without them.

Usage:
    python tools/sanitize-savedvariables.py <input.lua> <output.lua>
    python tools/sanitize-savedvariables.py <input.lua> <output.lua> --name Charactername --realm Realmname

Without --name/--realm the script reads them from the file's character
block, so the common case needs no arguments beyond the paths.
"""
import argparse
import re
import sys
from pathlib import Path

PLACEHOLDER_NAME = "Testcharacter"
PLACEHOLDER_REALM = "Testrealm"
PLACEHOLDER_GUID = "Player-0000-00000000"


def read_character_field(src, field):
    """Pull a field out of the top-level character block."""
    block = re.search(r'\["character"\]\s*=\s*\{(.*?)\n\},', src, re.S)
    if not block:
        return None
    found = re.search(r'\["%s"\]\s*=\s*"([^"]*)"' % field, block.group(1))
    return found.group(1) if found else None


def sanitize(src, name, realm, guid):
    replacements = 0

    if guid:
        src, n = re.subn(re.escape(guid), PLACEHOLDER_GUID, src)
        replacements += n
    # Catch any other player GUID, including ones from an older character.
    src, n = re.subn(r'Player-\d+-[0-9A-Fa-f]+', PLACEHOLDER_GUID, src)
    replacements += n

    if realm:
        src, n = re.subn(r'\b%s\b' % re.escape(realm), PLACEHOLDER_REALM, src)
        replacements += n

    # Name goes last and matches on a word boundary, because it also appears
    # inside quest and completion text where the client substituted it in.
    if name:
        src, n = re.subn(r'\b%s\b' % re.escape(name), PLACEHOLDER_NAME, src)
        replacements += n

    return src, replacements


def verify(src, name, realm, guid):
    """Fail loudly rather than publish a fixture that still has real data."""
    leaks = []
    for label, value in (("character name", name), ("realm", realm), ("guid", guid)):
        if value and re.search(r'\b%s\b' % re.escape(value), src):
            leaks.append("%s (%s) still present" % (label, value))
    if re.search(r'Player-\d+-[0-9A-Fa-f]+', src.replace(PLACEHOLDER_GUID, '')):
        leaks.append("an unreplaced player GUID is still present")
    return leaks


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('output')
    ap.add_argument('--name', help='character name to scrub (default: read from file)')
    ap.add_argument('--realm', help='realm name to scrub (default: read from file)')
    args = ap.parse_args(argv)

    src = Path(args.input).read_text(encoding='utf-8')

    name = args.name or read_character_field(src, 'name')
    realm = args.realm or read_character_field(src, 'realm')
    guid = read_character_field(src, 'guid')

    if not name:
        print('could not determine the character name; pass --name', file=sys.stderr)
        return 1

    print('scrubbing: name=%s realm=%s guid=%s' % (name, realm, guid))

    cleaned, count = sanitize(src, name, realm, guid)

    leaks = verify(cleaned, name, realm, guid)
    if leaks:
        print('REFUSING TO WRITE, personal data survived:', file=sys.stderr)
        for leak in leaks:
            print('  ' + leak, file=sys.stderr)
        return 1

    header = (
        '-- Sanitized Azeroth Chronicle capture, for companion importer tests.\n'
        '-- Player identity replaced with placeholders by '
        'tools/sanitize-savedvariables.py.\n'
        '-- NPC names and creature GUIDs are game content and are preserved.\n'
    )
    Path(args.output).write_text(header + cleaned, encoding='utf-8')

    print('wrote %s (%d substitutions)' % (args.output, count))
    return 0


if __name__ == '__main__':
    sys.exit(main())
