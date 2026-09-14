"""Tests for llm.py's Markdown cleanup.

_strip_markdown exists because a real generation during development ignored
the system prompt's "no markdown" instruction outright, producing headers
that would render as literal hash characters in the addon's plain-text
display. The first test below uses that actual output, not a synthetic
example, so a future change to the regexes has to keep handling the real
failure that motivated them, not just a tidy hypothetical.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'companion' / 'src'))

from azeroth_chronicle import llm  # noqa: E402

# A shortened, faithful excerpt of the real chapter generated during
# development before the prompt and this cleanup were tightened.
REAL_MARKDOWN_OUTPUT = """### A Chapter of the Forest

#### The Balance of Nature

Testcharacter approached Shadowglen, the air thick with the scent of ancient trees.

#### A Good Friend

On her way back, Testcharacter passed Dirania Silvershine, a familiar face."""


class TestStripMarkdown(unittest.TestCase):
    def test_the_actual_headers_observed_in_development_are_removed(self):
        cleaned = llm._strip_markdown(REAL_MARKDOWN_OUTPUT)
        self.assertNotIn('#', cleaned)
        self.assertIn('A Chapter of the Forest', cleaned)
        self.assertIn('The Balance of Nature', cleaned)
        self.assertIn('Testcharacter approached Shadowglen', cleaned)

    def test_bullet_lines_lose_the_marker_not_the_text(self):
        cleaned = llm._strip_markdown('- First point\n* Second point\n+ Third point')
        self.assertNotIn('- ', cleaned)
        self.assertIn('First point', cleaned)
        self.assertIn('Second point', cleaned)
        self.assertIn('Third point', cleaned)

    def test_bold_and_italic_markers_are_unwrapped_not_deleted(self):
        cleaned = llm._strip_markdown('This is **bold** and this is *italic* text.')
        self.assertNotIn('*', cleaned)
        self.assertIn('bold', cleaned)
        self.assertIn('italic', cleaned)

    def test_ordinary_prose_is_untouched(self):
        prose = ('Testcharacter spoke with Ilthalaine about the balance of nature,'
                 ' then set out for the northern cave.')
        self.assertEqual(llm._strip_markdown(prose), prose)

    def test_a_hash_used_as_ordinary_punctuation_is_only_stripped_at_line_start(self):
        # Headers are a line-start marker; a stray # mid-sentence (rare, but
        # not impossible in generated text) should not be touched.
        text = 'The reward was worth #1 in her book.'
        self.assertEqual(llm._strip_markdown(text), text)


if __name__ == '__main__':
    unittest.main()
