"""Tests for the addon Lua linter.

Run with: python -m unittest tools.test_check_lua -v  (from the repo root)
or:       python tools/test_check_lua.py

The linter is infrastructure other work depends on trusting, so its own
bugs deserve tests, not just manual fixture pokes. The `TestLongBrackets`
cases below are a real regression: the structure checker did not originally
know `[[ ]]` could be a string literal (only a `--[[` comment), so an
ordinary English "for" or "end" inside recap prose counted as a real
keyword and produced a false "unclosed block" on the very first generated
recap addon. See CLAUDE.md's addon-Lua section for the story.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    'check_lua', Path(__file__).resolve().parent / 'check-lua.py')
check_lua = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_lua)


def check_source(text):
    """Run the full file-level check against in-memory Lua source."""
    with tempfile.NamedTemporaryFile('w', suffix='.lua', delete=False,
                                     encoding='utf-8') as handle:
        handle.write(text)
        path = handle.name
    try:
        # check_file prints; redirect it away from the test's own stdout.
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = check_lua.check_file(path)
        return ok, buf.getvalue()
    finally:
        Path(path).unlink()


class TestLongBrackets(unittest.TestCase):
    """The regression this file exists to lock in."""

    def test_english_keywords_inside_a_string_literal_are_not_code(self):
        ok, output = check_source(
            'X = {\n'
            '["text"] = [[\n'
            'This uses the word for repeatedly: waiting for a friend, for the\n'
            'next task. It also has do, end, then, if, local and function as\n'
            'plain English words, not code.\n'
            ']],\n'
            '}\n')
        self.assertTrue(ok, output)

    def test_escalated_bracket_level_still_strips_correctly(self):
        # The string itself contains "]]", so the generator must have
        # widened to [=[ ... ]=] or higher; the linter must follow that.
        ok, output = check_source(
            'X = { ["text"] = [=[\n'
            'A sign here read "danger]] ahead" and the word for was near it.\n'
            ']=] }\n')
        self.assertTrue(ok, output)

    def test_long_comment_is_still_recognized(self):
        ok, output = check_source('--[[ this is a comment with for and end ]]\n'
                                  'local x = 1\n')
        self.assertTrue(ok, output)

    def test_a_real_unclosed_block_inside_prose_is_still_a_red_herring_not_a_hit(self):
        # A very deliberately adversarial case: prose that itself looks like
        # an unbalanced block, but is safely inside a string.
        ok, output = check_source(
            'X = { ["text"] = [[if this then that]] }\n')
        self.assertTrue(ok, output)


class TestRealBugsStillCaught(unittest.TestCase):
    """The fix must not have made the linter toothless."""

    def test_genuinely_unclosed_function_fails(self):
        ok, output = check_source(
            'local function f()\n  if x then\n    return 1\nend\n')
        self.assertFalse(ok)
        self.assertIn('unclosed', output.lower())

    def test_sandbox_blocked_call_fails(self):
        ok, output = check_source('local x = 1\nmath.randomseed(time())\n')
        self.assertFalse(ok)
        self.assertIn('randomseed', output)

    def test_forward_reference_fails(self):
        ok, output = check_source(
            'local function Caller()\n    Later(5)\nend\n\n'
            'local function Later(n)\n    return n\nend\n')
        self.assertFalse(ok)
        self.assertIn('nil global', output)

    def test_sandbox_name_inside_a_comment_is_not_flagged(self):
        ok, output = check_source(
            '-- math.randomseed(1) in a comment is fine\n'
            'local s = "math.randomseed(1) in a string is fine"\n')
        self.assertTrue(ok, output)


if __name__ == '__main__':
    unittest.main()
