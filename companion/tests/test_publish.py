"""Tests for publishing recaps into the game as a generated addon.

The interesting risk here is not the happy path, it is what happens when a
model's own prose contains something that looks like Lua syntax: a `]]`
sequence, an embedded quote, a backslash. Generating source code from
arbitrary text is exactly the kind of thing that works in every manual test
and breaks on the one input nobody tried.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'companion' / 'src'))

from azeroth_chronicle import db as db_module  # noqa: E402
from azeroth_chronicle import discovery  # noqa: E402
from azeroth_chronicle import publish as publish_module  # noqa: E402

MIGRATIONS = ROOT / 'companion' / 'migrations'


def make_row(text, summary_id='r1', character_id='Player-1-A', window_start=100,
            window_end=200, model='qwen2.5:7b-instruct', created_at=300,
            kind='session_recap'):
    return {
        'summary_id': summary_id, 'character_id': character_id, 'kind': kind,
        'window_start': window_start, 'window_end': window_end, 'model': model,
        'created_at': created_at, 'text': text,
    }


class TestLuaStringSafety(unittest.TestCase):
    """The generated Lua must parse regardless of what the model wrote."""

    def _wrapped_round_trips(self, text):
        """A crude but honest check: does the produced bracket actually
        close where we think it does, with the exact original text inside?
        """
        wrapped = publish_module._lua_long_bracket(text)
        # wrapped is "[<eq>[\n<text>]<eq>]"
        open_end = wrapped.index('[', 1) + 1
        self.assertEqual(wrapped[open_end], '\n')
        body = wrapped[open_end + 1:]
        close_marker = wrapped[wrapped.index('[') : open_end][::-1]  # e.g. "[==["
        # Find the actual closer by re-deriving it from the opener.
        eq_count = wrapped[1:open_end - 1].count('=')
        closer = ']' + '=' * eq_count + ']'
        self.assertTrue(body.endswith(closer))
        recovered = body[: -len(closer)]
        self.assertEqual(recovered, text)

    def test_plain_text(self):
        self._wrapped_round_trips('You went to Shadowglen and met Ilthalaine.')

    def test_embedded_double_closing_bracket(self):
        # The naive [[ ]] form would truncate here.
        self._wrapped_round_trips('The sign read: "danger]] ahead"')

    def test_embedded_quotes_and_backslashes(self):
        self._wrapped_round_trips('She said "go north" and \\ pointed the way.')

    def test_multiple_bracket_levels_needed(self):
        text = 'first ]] then ]=] then ]==] all in one'
        self._wrapped_round_trips(text)
        # Confirm it actually escalated rather than coincidentally working.
        wrapped = publish_module._lua_long_bracket(text)
        self.assertIn('[===[', wrapped)

    def test_empty_and_none_text(self):
        self._wrapped_round_trips('')
        # None must not raise; treated as empty.
        wrapped = publish_module._lua_long_bracket(None)
        self.assertIn('[[', wrapped)

    def test_short_lua_string_escapes_quotes_and_backslashes(self):
        raw = publish_module._lua_string('a "quoted" \\ value\nwith a newline')
        self.assertNotIn('\n', raw.replace('\\n', ''))  # real newline must be escaped
        self.assertTrue(raw.startswith('"') and raw.endswith('"'))

    def test_short_lua_string_handles_none(self):
        self.assertEqual(publish_module._lua_string(None), '""')


class TestBuildDataFile(unittest.TestCase):
    def test_groups_by_character(self):
        rows = {
            'Player-1-A': [make_row('recap a', character_id='Player-1-A')],
            'Player-2-B': [make_row('recap b', summary_id='r2', character_id='Player-2-B')],
        }
        lua = publish_module.build_data_file(rows)
        self.assertIn('Player-1-A', lua)
        self.assertIn('Player-2-B', lua)
        self.assertIn('AzerothChronicleSummariesDB', lua)

    def test_newest_recap_sorts_first(self):
        rows = {'Player-1-A': [
            make_row('older', summary_id='old', window_end=100),
            make_row('newer', summary_id='new', window_end=500),
        ]}
        lua = publish_module.build_data_file(rows)
        self.assertLess(lua.index('newer'), lua.index('older'))

    def test_declares_itself_generated(self):
        lua = publish_module.build_data_file({})
        self.assertIn('Generated', lua)
        self.assertIn('Do not edit', lua)

    def test_empty_table_is_still_valid_shape(self):
        lua = publish_module.build_data_file({})
        self.assertIn('AzerothChronicleSummariesDB = {', lua)
        self.assertTrue(lua.strip().endswith('}'))


class TestBuildToc(unittest.TestCase):
    def test_carries_the_given_interface_version(self):
        toc = publish_module.build_toc('11509')
        self.assertIn('## Interface: 11509', toc)
        self.assertIn(publish_module.DATA_FILE, toc)


class TestReadInterfaceVersion(unittest.TestCase):
    def test_reads_from_sibling_main_addon(self):
        with tempfile.TemporaryDirectory() as tmp:
            addons_dir = Path(tmp)
            main = addons_dir / 'AzerothChronicle'
            main.mkdir()
            (main / 'AzerothChronicle.toc').write_text(
                '## Interface: 99999\n## Title: X\n', encoding='utf-8')
            self.assertEqual(
                publish_module.read_interface_version(addons_dir), '99999')

    def test_missing_main_addon_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(publish_module.read_interface_version(Path(tmp)))


class TestPublishEndToEnd(unittest.TestCase):
    """Exercises the real discovery + write path against a fake install."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wow_root = Path(self.tmp.name) / 'World of Warcraft'
        addons = self.wow_root / '_classic_era_' / 'Interface' / 'AddOns'
        main_addon = addons / 'AzerothChronicle'
        main_addon.mkdir(parents=True)
        (main_addon / 'AzerothChronicle.toc').write_text(
            '## Interface: 11509\n## Title: Azeroth Chronicle\n', encoding='utf-8')

        self.conn = db_module.open_database(
            Path(self.tmp.name) / 'test.sqlite3', MIGRATIONS)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_publish_writes_toc_and_data_file(self):
        self.conn.execute(
            'INSERT INTO summaries (summary_id, character_id, kind, window_start,'
            ' window_end, model, created_at, text) VALUES (?,?,?,?,?,?,?,?)',
            ('r1', 'Player-1-A', 'session_recap', 100, 200, 'qwen2.5:7b-instruct',
             300, 'You went to Shadowglen.'))
        self.conn.commit()

        targets = publish_module.publish(self.conn, wow_path=str(self.wow_root))
        self.assertEqual(len(targets), 1)

        addon_dir = targets[0] / publish_module.ADDON_NAME
        self.assertTrue((addon_dir / publish_module.TOC_FILE).is_file())
        data_file = addon_dir / publish_module.DATA_FILE
        self.assertTrue(data_file.is_file())

        content = data_file.read_text(encoding='utf-8')
        self.assertIn('You went to Shadowglen.', content)
        self.assertIn('## Interface: 11509' in
                      (addon_dir / publish_module.TOC_FILE).read_text(encoding='utf-8'), [True])

    def test_publish_with_no_main_addon_finds_nothing(self):
        with tempfile.TemporaryDirectory() as empty_root:
            targets = publish_module.publish(self.conn, wow_path=empty_root)
            self.assertEqual(targets, [])

    def test_republish_overwrites_rather_than_duplicates(self):
        self.conn.execute(
            'INSERT INTO summaries (summary_id, character_id, kind, window_start,'
            ' window_end, model, created_at, text) VALUES (?,?,?,?,?,?,?,?)',
            ('r1', 'Player-1-A', 'session_recap', 100, 200, 'm', 300, 'first'))
        self.conn.commit()

        publish_module.publish(self.conn, wow_path=str(self.wow_root))
        publish_module.publish(self.conn, wow_path=str(self.wow_root))

        data_file = (discovery.find_addon_installations(str(self.wow_root))[0]
                    / publish_module.ADDON_NAME / publish_module.DATA_FILE)
        content = data_file.read_text(encoding='utf-8')
        self.assertEqual(content.count('"first"'), 0)  # it's a long-bracket string
        self.assertEqual(content.count('first'), 1, 'publishing twice must not duplicate')


if __name__ == '__main__':
    unittest.main()
