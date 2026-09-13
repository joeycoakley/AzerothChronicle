"""Pre-reload sanity check for Azeroth Chronicle addon Lua.

There is no Lua interpreter on a typical WoW development machine, and a
broken addon costs a full login cycle to discover. This catches the two
cheap classes of mistake before the game ever sees the file:

  1. Block structure. Unbalanced end/then/parens, which stop the file from
     loading at all.
  2. Calls to globals that WoW's Lua sandbox does not expose. These parse
     fine and fail at runtime with "attempt to call a nil value", which is
     exactly how math.randomseed broke ADDON_LOADED during V0 bring-up.

This is a lint, not a parser. It cannot prove the addon works. The real
test is still /reload in the client.

Usage:
    python tools/check-lua.py addon/AzerothChronicle/Core.lua
    python tools/check-lua.py            (checks every .lua under addon/)
"""
import re
import sys
from pathlib import Path

BACKSLASH = chr(92)

# Globals that exist in standard Lua but are removed or restricted inside
# WoW's addon sandbox. Calling any of these fails at runtime, not load time.
SANDBOX_BLOCKED = {
    'math.randomseed': 'not exposed by WoW; the client seeds math.random itself',
    'os.execute': 'no process execution in the addon sandbox',
    'os.exit': 'no process control in the addon sandbox',
    'os.getenv': 'no environment access in the addon sandbox',
    'os.remove': 'no filesystem access in the addon sandbox',
    'os.rename': 'no filesystem access in the addon sandbox',
    'os.tmpname': 'no filesystem access in the addon sandbox',
    'io.open': 'no filesystem access; SavedVariables is the only bridge',
    'io.read': 'no filesystem access in the addon sandbox',
    'io.write': 'no filesystem access in the addon sandbox',
    'io.lines': 'no filesystem access in the addon sandbox',
    'require': 'addon files are loaded via the TOC, not require',
    'dofile': 'no filesystem access in the addon sandbox',
    'loadfile': 'no filesystem access in the addon sandbox',
    'package': 'module system is not available to addons',
    'debug.sethook': 'restricted in the addon sandbox',
    'debug.setlocal': 'restricted in the addon sandbox',
}


def strip_comments_and_strings(src):
    """Blank out comments and string literals so keyword scanning is honest."""
    out = []
    i, n = 0, len(src)
    while i < n:
        if src[i:i + 2] == '--':
            if src[i + 2:i + 4] == '[[':
                end = src.find(']]', i + 4)
                i = n if end == -1 else end + 2
            else:
                end = src.find('\n', i)
                i = n if end == -1 else end
            continue
        ch = src[i]
        if ch in ('"', "'"):
            quote, i = ch, i + 1
            while i < n:
                if src[i] == BACKSLASH:
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            out.append('""')
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def check_structure(code):
    problems = []
    tokens = re.findall(r'\b\w+\b', code)
    stack = []

    for tok in tokens:
        if tok in ('for', 'while'):
            stack.append(tok)
        elif tok == 'do':
            if not (stack and stack[-1] in ('for', 'while')):
                stack.append('do')
        elif tok in ('function', 'if', 'repeat'):
            stack.append(tok)
        elif tok == 'end':
            if stack:
                stack.pop()
            else:
                problems.append('unmatched "end"')
        elif tok == 'until':
            if stack and stack[-1] == 'repeat':
                stack.pop()
            else:
                problems.append('"until" without "repeat"')

    if stack:
        problems.append('unclosed blocks at end of file: %s' % ', '.join(stack))

    ifs = tokens.count('if') + tokens.count('elseif')
    thens = tokens.count('then')
    if ifs != thens:
        problems.append('if/elseif count (%d) does not match then count (%d)' % (ifs, thens))

    if code.count('(') != code.count(')'):
        problems.append('unbalanced parentheses (%+d)' % (code.count('(') - code.count(')')))
    if code.count('{') != code.count('}'):
        problems.append('unbalanced braces (%+d)' % (code.count('{') - code.count('}')))

    return problems


def check_sandbox(raw):
    """Flag blocked globals, reported with the original file's line numbers."""
    problems = []
    lines = raw.splitlines()
    for lineno, line in enumerate(lines, 1):
        stripped = strip_comments_and_strings(line)
        for name, why in SANDBOX_BLOCKED.items():
            if re.search(r'(?<![\w.])' + re.escape(name) + r'\s*[(.]', stripped):
                problems.append('line %d: %s is %s' % (lineno, name, why))
    return problems


def check_forward_references(raw):
    """Flag a file-level local called before the line that declares it.

    In Lua a name used above its `local` declaration silently resolves to a
    global, which is nil. Nothing errors at load; the call just fails at
    runtime, and WoW hides Lua errors by default, so it fails invisibly.

    This bit twice during development, both times in the UI where one
    renderer referenced another defined further down the file.
    """
    problems = []
    lines = strip_comments_and_strings(raw).split('\n')

    declared = {}
    for number, line in enumerate(lines, 1):
        stripped = line.strip()

        # `local A, B` with no assignment: a forward declaration.
        match = re.match(r'^local ([A-Za-z_][\w, ]*)$', stripped)
        if match and '=' not in stripped:
            for name in match.group(1).split(','):
                declared.setdefault(name.strip(), number)

        match = re.match(r'^local function (\w+)', stripped)
        if match:
            declared.setdefault(match.group(1), number)

    for name, declaration_line in declared.items():
        if not name or len(name) < 3:
            continue
        pattern = re.compile(r'(?<![\w.])' + re.escape(name) + r'\s*\(')
        for number, line in enumerate(lines[:declaration_line - 1], 1):
            if pattern.search(line):
                problems.append(
                    '%s is called on line %d but declared on line %d, '
                    'so that call resolves to a nil global'
                    % (name, number, declaration_line))
                break

    return problems


def check_file(path):
    raw = Path(path).read_text(encoding='utf-8')
    code = strip_comments_and_strings(raw)

    problems = (check_structure(code) + check_sandbox(raw)
                + check_forward_references(raw))

    if problems:
        print('FAIL %s' % path)
        for p in problems:
            print('     %s' % p)
    else:
        print('ok   %s (%d lines)' % (path, len(raw.splitlines())))
    return not problems


def main(argv):
    if len(argv) > 1:
        targets = [Path(a) for a in argv[1:]]
    else:
        root = Path(__file__).resolve().parent.parent
        targets = sorted((root / 'addon').rglob('*.lua'))

    if not targets:
        print('no Lua files found')
        return 1

    return 0 if all(check_file(t) for t in targets) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
