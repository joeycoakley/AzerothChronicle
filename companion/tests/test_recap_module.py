"""Tests for recap.py (shared generate-and-save logic) and
context.find_closed_sessions (the watcher's "is this session over" rule).

llm.summarize is mocked throughout: these tests are about caching, session
closure, and the on_will_generate callback contract, not about the model.
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
from azeroth_chronicle import importer  # noqa: E402
from azeroth_chronicle import llm as llm_module  # noqa: E402
from azeroth_chronicle import recap as recap_module  # noqa: E402

MIGRATIONS = ROOT / 'companion' / 'migrations'
CHARACTER = {'guid': 'Player-1-A', 'name': 'Tester', 'realm': 'Realm', 'class': 'HUNTER'}
HOUR = 3600


def event(event_id, event_type, timestamp, **extra):
    payload = {
        'id': event_id, 'type': event_type, 'timestamp': timestamp,
        'schemaVersion': 1, 'sessionId': 's1', 'character': CHARACTER,
        'location': {'zone': 'Teldrassil'},
    }
    payload.update(extra)
    return payload


def fake_summarize(text, **kwargs):
    return {'text': 'a recap', 'model': 'fake-model',
            'input_tokens': 10, 'output_tokens': 5}


class RecapTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db_module.open_database(
            Path(self.tmp.name) / 'test.sqlite3', MIGRATIONS)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def ingest(self, events):
        importer.ingest_events(self.conn, {'character': CHARACTER, 'events': events})
        importer.materialize(self.conn)
        self.conn.commit()


class TestGenerateAndSaveRecap(RecapTestCase):
    def setUp(self):
        super().setUp()
        self.ingest([
            event('e1', 'QUEST_DETAIL', 1000,
                  quest={'id': 1, 'title': 'A Quest', 'description': 'Do a thing.'}),
        ])
        self.session = context_module.find_play_sessions(self.conn)[0]

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_first_call_generates_and_saves(self, mock_summarize):
        outcome = recap_module.generate_and_save_recap(self.conn, self.session)
        self.assertFalse(outcome.cached)
        self.assertEqual(outcome.text, 'a recap')
        mock_summarize.assert_called_once()

        row = self.conn.execute('SELECT COUNT(*) AS n FROM summaries').fetchone()
        self.assertEqual(row['n'], 1)

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_second_call_hits_cache_without_calling_the_model(self, mock_summarize):
        recap_module.generate_and_save_recap(self.conn, self.session)
        outcome = recap_module.generate_and_save_recap(self.conn, self.session)

        self.assertTrue(outcome.cached)
        self.assertEqual(outcome.text, 'a recap')
        mock_summarize.assert_called_once()  # not called again

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_force_regenerates_even_when_cached(self, mock_summarize):
        recap_module.generate_and_save_recap(self.conn, self.session)
        outcome = recap_module.generate_and_save_recap(
            self.conn, self.session, force=True)

        self.assertFalse(outcome.cached)
        self.assertEqual(mock_summarize.call_count, 2)

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_on_will_generate_fires_only_when_actually_generating(self, mock_summarize):
        calls = []
        recap_module.generate_and_save_recap(
            self.conn, self.session, on_will_generate=lambda: calls.append(1))
        self.assertEqual(calls, [1], 'must fire on a real generation')

        recap_module.generate_and_save_recap(
            self.conn, self.session, on_will_generate=lambda: calls.append(1))
        self.assertEqual(calls, [1], 'must NOT fire again on a cache hit')

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_no_save_does_not_persist(self, mock_summarize):
        recap_module.generate_and_save_recap(self.conn, self.session, save=False)
        row = self.conn.execute('SELECT COUNT(*) AS n FROM summaries').fetchone()
        self.assertEqual(row['n'], 0)

    def test_llm_failure_propagates_rather_than_being_swallowed(self):
        with mock.patch.object(llm_module, 'summarize',
                               side_effect=llm_module.LlmUnavailable('down')):
            with self.assertRaises(llm_module.LlmUnavailable):
                recap_module.generate_and_save_recap(self.conn, self.session)


class TestFindClosedSessions(RecapTestCase):
    def test_only_session_stays_open_while_recent(self):
        self.ingest([event('e1', 'GOSSIP_SHOW', 1000, gossip={'text': 'a'})])
        closed = context_module.find_closed_sessions(
            self.conn, gap_seconds=HOUR, now=1000 + 10)
        self.assertEqual(closed, [], 'a session moments old is not closed')

    def test_only_session_closes_after_the_gap_elapses_on_the_clock(self):
        # No new event ever arrives; wall-clock time passing alone must be
        # enough, or a player who logs off for good is never recapped.
        self.ingest([event('e1', 'GOSSIP_SHOW', 1000, gossip={'text': 'a'})])
        closed = context_module.find_closed_sessions(
            self.conn, gap_seconds=HOUR, now=1000 + 2 * HOUR)
        self.assertEqual(len(closed), 1)

    def test_earlier_sessions_are_always_closed(self):
        self.ingest([
            event('e1', 'GOSSIP_SHOW', 1000, gossip={'text': 'a'}),
            event('e2', 'GOSSIP_SHOW', 1000 + 5 * HOUR, gossip={'text': 'b'}),
        ])
        # "now" is right at the second session's last event: it is not
        # closed by time, but the first one must be closed regardless,
        # because nothing can ever be appended to it again.
        closed = context_module.find_closed_sessions(
            self.conn, gap_seconds=2 * HOUR, now=1000 + 5 * HOUR)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].event_count, 1)

    def test_no_events_returns_empty(self):
        self.assertEqual(context_module.find_closed_sessions(self.conn), [])


if __name__ == '__main__':
    unittest.main()
