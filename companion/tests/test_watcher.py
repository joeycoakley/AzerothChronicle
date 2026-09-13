"""Tests for the watcher's core pass logic (watcher.run_once).

Builds a real fake WoW installation (a SavedVariables file plus an AddOns
directory holding the main addon) so this exercises the actual
discover -> import -> recap -> publish chain, not a mocked stand-in for it.
Only the model call is mocked; everything else is the real code.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'companion' / 'src'))

from azeroth_chronicle import context as context_module  # noqa: E402
from azeroth_chronicle import db as db_module  # noqa: E402
from azeroth_chronicle import llm as llm_module  # noqa: E402
from azeroth_chronicle import publish as publish_module  # noqa: E402
from azeroth_chronicle import watcher  # noqa: E402

MIGRATIONS = ROOT / 'companion' / 'migrations'
HOUR = 3600


def fake_summarize(text, **kwargs):
    return {'text': 'a generated recap', 'model': 'fake-model',
            'input_tokens': 10, 'output_tokens': 5}


def saved_variables_lua(events_lua, recap_requested_at=None):
    requested_line = ('["recapRequestedAt"] = %d,\n' % recap_requested_at
                      if recap_requested_at is not None else '')
    return (
        'AzerothChronicleDB = {\n'
        '["schemaVersion"] = 1,\n'
        '["character"] = { ["guid"] = "Player-1-A", ["name"] = "Tester",'
        ' ["realm"] = "Realm", ["class"] = "HUNTER" },\n'
        '["sessions"] = {},\n'
        '["events"] = {\n' + events_lua + '\n},\n'
        '["settings"] = {},\n'
        + requested_line +
        '}\n')


def quest_event_lua(event_id, timestamp, quest_id=1, title='A Quest'):
    return (
        '{ ["id"] = "%s", ["type"] = "QUEST_DETAIL", ["timestamp"] = %d,'
        ' ["schemaVersion"] = 1, ["sessionId"] = "s1",'
        ' ["character"] = { ["guid"] = "Player-1-A" },'
        ' ["location"] = { ["zone"] = "Teldrassil" },'
        ' ["quest"] = { ["id"] = %d, ["title"] = "%s",'
        ' ["description"] = "Do a thing." } },'
        % (event_id, timestamp, quest_id, title))


class WatcherTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wow_root = Path(self.tmp.name) / 'World of Warcraft'

        self.sv_dir = (self.wow_root / '_classic_era_' / 'WTF' / 'Account'
                       / 'ACCT1' / 'Realm' / 'Tester' / 'SavedVariables')
        self.sv_dir.mkdir(parents=True)
        self.sv_path = self.sv_dir / 'AzerothChronicle.lua'

        addons = self.wow_root / '_classic_era_' / 'Interface' / 'AddOns'
        main_addon = addons / 'AzerothChronicle'
        main_addon.mkdir(parents=True)
        (main_addon / 'AzerothChronicle.toc').write_text(
            '## Interface: 11509\n## Title: Azeroth Chronicle\n', encoding='utf-8')

        self.db_path = Path(self.tmp.name) / 'test.sqlite3'
        # open once to run migrations; watcher.run_forever would do this
        # itself, but run_once takes an existing connection.
        self.conn = db_module.open_database(self.db_path, MIGRATIONS)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def write_events(self, events_lua, recap_requested_at=None):
        self.sv_path.write_text(
            saved_variables_lua(events_lua, recap_requested_at), encoding='utf-8')


class TestRunOnce(WatcherTestCase):
    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_imports_recaps_and_publishes_a_closed_session(self, mock_summarize):
        old_timestamp = 1000
        self.write_events(quest_event_lua('e1', old_timestamp))

        # far enough in the past, relative to "now" inside run_once (real
        # wall clock), that the session reads as closed
        with mock.patch('time.time', return_value=old_timestamp + 10 * HOUR):
            result = watcher.run_once(self.conn, wow_path=str(self.wow_root))

        self.assertEqual(result.imported_events, 1)
        self.assertEqual(len(result.new_recaps), 1)
        self.assertEqual(len(result.published_to), 1)
        mock_summarize.assert_called_once()

        data_file = (Path(result.published_to[0]) / publish_module.ADDON_NAME
                    / publish_module.DATA_FILE)
        self.assertIn('a generated recap', data_file.read_text(encoding='utf-8'))

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_open_session_is_not_recapped(self, mock_summarize):
        self.write_events(quest_event_lua('e1', 1000))

        with mock.patch('time.time', return_value=1000 + 10):  # moments later
            result = watcher.run_once(self.conn, wow_path=str(self.wow_root))

        self.assertEqual(result.new_recaps, [])
        mock_summarize.assert_not_called()

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_in_game_recap_request_closes_the_session_immediately(self, mock_summarize):
        # The full round trip: real Lua text written by the addon's
        # RequestRecap, parsed by luaparse.py, honored by
        # find_closed_sessions, without waiting out the wall-clock gap.
        recent_timestamp = 1000
        self.write_events(
            quest_event_lua('e1', recent_timestamp),
            recap_requested_at=recent_timestamp + 5)

        with mock.patch('time.time', return_value=recent_timestamp + 10):  # moments later
            result = watcher.run_once(self.conn, wow_path=str(self.wow_root))

        self.assertEqual(len(result.new_recaps), 1,
                         'an explicit request must close the session despite the gap')
        mock_summarize.assert_called_once()
        self.assertEqual(len(result.published_to), 1)

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_second_pass_does_not_regenerate_or_republish(self, mock_summarize):
        old_timestamp = 1000
        self.write_events(quest_event_lua('e1', old_timestamp))

        with mock.patch('time.time', return_value=old_timestamp + 10 * HOUR):
            watcher.run_once(self.conn, wow_path=str(self.wow_root))
            second = watcher.run_once(self.conn, wow_path=str(self.wow_root))

        self.assertEqual(second.new_recaps, [], 'nothing new to recap on pass two')
        self.assertEqual(second.published_to, [], 'must not republish when nothing changed')
        mock_summarize.assert_called_once()  # still just the one real call

    def test_llm_unavailable_does_not_crash_the_pass(self):
        self.write_events(quest_event_lua('e1', 1000))
        with mock.patch.object(llm_module, 'summarize',
                               side_effect=llm_module.LlmUnavailable('no server')):
            with mock.patch('time.time', return_value=1000 + 10 * HOUR):
                result = watcher.run_once(self.conn, wow_path=str(self.wow_root))

        self.assertEqual(result.new_recaps, [])
        self.assertEqual(len(result.recap_errors), 1)
        self.assertEqual(result.published_to, [], 'nothing new, nothing to publish')

    def test_malformed_saved_variables_does_not_crash_the_pass(self):
        self.sv_path.write_text('this is not valid lua {{{', encoding='utf-8')
        result = watcher.run_once(self.conn, wow_path=str(self.wow_root))
        self.assertEqual(len(result.import_errors), 1)

    def test_no_saved_variables_file_is_a_quiet_no_op(self):
        result = watcher.run_once(self.conn, wow_path=str(self.wow_root))
        self.assertEqual(result.imported_events, 0)
        self.assertEqual(result.import_errors, [])
        self.assertFalse(result.changed)

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_two_closed_sessions_both_get_recapped_in_one_pass(self, mock_summarize):
        events = (quest_event_lua('e1', 1000, quest_id=1, title='First')
                 + quest_event_lua('e2', 1000 + 5 * HOUR, quest_id=2, title='Second'))
        self.write_events(events)

        with mock.patch('time.time', return_value=1000 + 20 * HOUR):
            result = watcher.run_once(self.conn, wow_path=str(self.wow_root),
                                      gap_seconds=2 * HOUR)

        self.assertEqual(len(result.new_recaps), 2)
        self.assertEqual(mock_summarize.call_count, 2)
        self.assertEqual(len(result.published_to), 1, 'one publish call for both')


if __name__ == '__main__':
    unittest.main()
