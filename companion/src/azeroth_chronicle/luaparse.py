"""Parse the subset of Lua that WoW writes into SavedVariables files.

WoW serializes SavedVariables as executable Lua source, not as a data
format, so the importer has to read it back. A real Lua interpreter would
work but pulls in a dependency, and executing a file written by a game
client is a worse idea than parsing it.

The emitted subset is small and predictable:

    NAME = {
    ["key"] = "string",
    ["nested"] = {
    1,
    2,
    },
    }

Tables mix a hash part (bracketed keys) with an array part (bare values).
Values are strings, numbers, booleans, or nested tables. nil is never
written, it just makes the key absent, which is why so many captured
events legitimately lack fields.

This parser is deliberately strict: it raises on anything it does not
recognize rather than guessing, because silently misreading a player's
history is worse than refusing to read it.
"""


class LuaParseError(Exception):
    """Raised when the input is not the Lua subset we expect."""

    def __init__(self, message, text=None, pos=None):
        if text is not None and pos is not None:
            line = text.count('\n', 0, pos) + 1
            col = pos - (text.rfind('\n', 0, pos) + 1) + 1
            snippet = text[pos:pos + 40].replace('\n', '\\n')
            message = '%s at line %d column %d, near: %s' % (message, line, col, snippet)
        super().__init__(message)


_ESCAPES = {
    'n': '\n',
    't': '\t',
    'r': '\r',
    'a': '\a',
    'b': '\b',
    'f': '\f',
    'v': '\v',
    '\\': '\\',
    '"': '"',
    "'": "'",
    '\n': '\n',
}


class _Parser:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.len = len(text)

    # -- basic scanning ------------------------------------------------

    def error(self, message):
        return LuaParseError(message, self.text, self.pos)

    def peek(self):
        return self.text[self.pos] if self.pos < self.len else ''

    def skip_trivia(self):
        """Skip whitespace and comments, including the header we write."""
        while self.pos < self.len:
            ch = self.text[self.pos]
            if ch in ' \t\r\n':
                self.pos += 1
                continue
            if self.text.startswith('--', self.pos):
                if self.text.startswith('--[[', self.pos):
                    end = self.text.find(']]', self.pos + 4)
                    self.pos = self.len if end == -1 else end + 2
                else:
                    end = self.text.find('\n', self.pos)
                    self.pos = self.len if end == -1 else end + 1
                continue
            break

    def expect(self, ch):
        self.skip_trivia()
        if self.peek() != ch:
            raise self.error('expected %r' % ch)
        self.pos += 1

    # -- values --------------------------------------------------------

    def parse_value(self):
        self.skip_trivia()
        ch = self.peek()
        if ch == '':
            raise self.error('unexpected end of input')
        if ch == '{':
            return self.parse_table()
        if ch in '"\'':
            return self.parse_string()
        if self.text.startswith('true', self.pos):
            self.pos += 4
            return True
        if self.text.startswith('false', self.pos):
            self.pos += 5
            return False
        if self.text.startswith('nil', self.pos):
            self.pos += 3
            return None
        if ch == '-' or ch.isdigit() or ch == '.':
            return self.parse_number()
        raise self.error('unrecognized value')

    def parse_string(self):
        quote = self.peek()
        self.pos += 1
        out = []
        while True:
            if self.pos >= self.len:
                raise self.error('unterminated string')
            ch = self.text[self.pos]
            if ch == '\\':
                self.pos += 1
                if self.pos >= self.len:
                    raise self.error('unterminated escape')
                esc = self.text[self.pos]
                if esc.isdigit():
                    # Numeric escape: up to three decimal digits.
                    digits = ''
                    while self.pos < self.len and self.text[self.pos].isdigit() and len(digits) < 3:
                        digits += self.text[self.pos]
                        self.pos += 1
                    out.append(chr(int(digits)))
                    continue
                out.append(_ESCAPES.get(esc, esc))
                self.pos += 1
                continue
            if ch == quote:
                self.pos += 1
                return ''.join(out)
            out.append(ch)
            self.pos += 1

    def parse_number(self):
        start = self.pos
        if self.peek() == '-':
            self.pos += 1
        if self.text.startswith('0x', self.pos) or self.text.startswith('0X', self.pos):
            self.pos += 2
            while self.pos < self.len and self.text[self.pos] in '0123456789abcdefABCDEF':
                self.pos += 1
            return int(self.text[start:self.pos], 16)

        seen_dot = False
        seen_exp = False
        while self.pos < self.len:
            ch = self.text[self.pos]
            if ch.isdigit():
                self.pos += 1
            elif ch == '.' and not seen_dot and not seen_exp:
                seen_dot = True
                self.pos += 1
            elif ch in 'eE' and not seen_exp:
                seen_exp = True
                self.pos += 1
                if self.pos < self.len and self.text[self.pos] in '+-':
                    self.pos += 1
            else:
                break

        raw = self.text[start:self.pos]
        if raw in ('', '-'):
            raise self.error('malformed number')
        if seen_dot or seen_exp:
            return float(raw)
        return int(raw)

    def parse_key(self):
        """A bracketed key: ["name"] or [3]."""
        self.expect('[')
        key = self.parse_value()
        self.expect(']')
        self.skip_trivia()
        if self.peek() != '=':
            raise self.error('expected = after table key')
        self.pos += 1
        return key

    def parse_table(self):
        self.expect('{')
        hash_part = {}
        array_part = []

        while True:
            self.skip_trivia()
            ch = self.peek()
            if ch == '':
                raise self.error('unterminated table')
            if ch == '}':
                self.pos += 1
                break
            if ch == '[':
                key = self.parse_key()
                hash_part[key] = self.parse_value()
            else:
                array_part.append(self.parse_value())

            self.skip_trivia()
            if self.peek() in ',;':
                self.pos += 1

        if array_part and not hash_part:
            return array_part
        if array_part:
            # Mixed table: expose the array part under integer keys, which
            # is what Lua itself would do, so no captured data is dropped.
            for index, value in enumerate(array_part, start=1):
                hash_part.setdefault(index, value)
        return hash_part

    # -- top level -----------------------------------------------------

    def parse_assignments(self):
        """Read `NAME = value` statements until the input is exhausted."""
        result = {}
        while True:
            self.skip_trivia()
            if self.pos >= self.len:
                return result

            start = self.pos
            while self.pos < self.len and (self.text[self.pos].isalnum() or self.text[self.pos] == '_'):
                self.pos += 1
            name = self.text[start:self.pos]
            if not name:
                raise self.error('expected a variable name')

            self.skip_trivia()
            if self.peek() != '=':
                raise self.error('expected = after variable name %r' % name)
            self.pos += 1

            result[name] = self.parse_value()

            self.skip_trivia()
            if self.peek() == ';':
                self.pos += 1


def loads(text):
    """Parse SavedVariables source into a dict of variable name to value."""
    return _Parser(text).parse_assignments()


def load_file(path, encoding='utf-8'):
    """Parse a SavedVariables file.

    WoW writes UTF-8, but a corrupted or partially written file is a real
    possibility after a crash, so decoding errors are surfaced clearly
    rather than producing mojibake that fails later during import.
    """
    with open(path, 'rb') as handle:
        raw = handle.read()
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise LuaParseError('%s is not valid %s: %s' % (path, encoding, exc))
    return loads(text)
