"""Tests for recap retrieval.

None of these touch the network. The point of the module under test is that
it decides what a model is allowed to see, so the tests are about what ends
up in the context and, more importantly, what does not.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'companion' / 'src'))

from azeroth_chronicle import context as context_module  # noqa: E402
from azeroth_chronicle import db as db_module  # noqa: E402
from azeroth_chronicle import importer  # noqa: E402

MIGRATIONS = ROOT / 'companion' / 'migrations'
CHARACTER = {
    'guid': 'Player-1-A', 'name': 'Tester', 'realm': 'Realm',
    'class': 'HUNTER', 'race': 'NightElf', 'faction': 'Alliance', 'level': 5,
}

HOUR = 3600


def event(event_id, event_type, timestamp, **extra):
    payload = {
        'id': event_id, 'type': event_type, 'timestamp': timestamp,
        'schemaVersion': 1, 'sessionId': 's1', 'character': CHARACTER,
        'location': {'zone': 'Teldrassil', 'mapId': 1438},
    }
    payload.update(extra)
    return payload


class ContextTestCase(unittest.TestCase):
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


class TestPlaySessions(ContextTestCase):
    def test_a_long_gap_starts_a_new_session(self):
        self.ingest([
            event('e1', 'GOSSIP_SHOW', 1000, gossip={'text': 'a'}),
            event('e2', 'GOSSIP_SHOW', 2000, gossip={'text': 'b'}),
            event('e3', 'GOSSIP_SHOW', 2000 + 5 * HOUR, gossip={'text': 'c'}),
        ])
        sessions = context_module.find_play_sessions(self.conn, gap_seconds=2 * HOUR)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].event_count, 2)
        self.assertEqual(sessions[1].event_count, 1)

    def test_a_short_break_does_not(self):
        self.ingest([
            event('e1', 'GOSSIP_SHOW', 1000, gossip={'text': 'a'}),
            event('e2', 'GOSSIP_SHOW', 1000 + HOUR, gossip={'text': 'b'}),
        ])
        sessions = context_module.find_play_sessions(self.conn, gap_seconds=2 * HOUR)
        self.assertEqual(len(sessions), 1)

    def test_addon_reloads_do_not_split_a_session(self):
        # The addon records a session per reload. A player reloading four
        # times in an evening played once, and should get one recap.
        events = []
        for i in range(4):
            events.append(event('e%d' % i, 'GOSSIP_SHOW', 1000 + i * 60,
                                sessionId='reload-%d' % i, gossip={'text': 'x%d' % i}))
        self.ingest(events)
        sessions = context_module.find_play_sessions(self.conn, gap_seconds=2 * HOUR)
        self.assertEqual(len(sessions), 1)


class TestRecapContext(ContextTestCase):
    def setUp(self):
        super().setUp()
        self.ingest([
            event('e1', 'QUEST_DETAIL', 1000,
                  source={'name': 'Ilthalaine', 'guid': 'Creature-0-1-1-1-2079-0'},
                  quest={'id': 456, 'title': 'The Balance of Nature',
                         'description': 'Thin the boar population.',
                         'objectivesText': 'Kill 7 boars.'}),
            event('e2', 'QUEST_ACCEPTED', 1010, quest={'id': 456}),
            event('e3', 'QUEST_PROGRESS', 1020,
                  quest={'id': 456, 'progressText': 'Work remains.'}),
            event('e4', 'QUEST_PROGRESS', 1030,
                  quest={'id': 456, 'progressText': 'Work remains.'}),
            event('e5', 'QUEST_COMPLETE', 1040,
                  quest={'id': 456, 'completionText': 'Well done.'}),
            event('e6', 'QUEST_TURNED_IN', 1050,
                  quest={'id': 456, 'xpReward': 170, 'moneyReward': 35}),
        ])
        self.session = context_module.find_play_sessions(self.conn)[0]

    def build(self):
        return context_module.build_recap_context(self.conn, self.session)

    def test_captured_text_reaches_the_context(self):
        text, _, _ = self.build()
        self.assertIn('Thin the boar population.', text)
        self.assertIn('Kill 7 boars.', text)
        self.assertIn('Well done.', text)
        self.assertIn('Ilthalaine', text)
        self.assertIn('170 experience', text)

    def test_repeated_dialogue_appears_once(self):
        text, _, _ = self.build()
        self.assertEqual(text.count('Work remains.'), 1)

    def test_completion_text_is_not_duplicated(self):
        # It is stored both as a quest field and as a dialogue line.
        text, _, _ = self.build()
        self.assertEqual(text.count('Well done.'), 1)

    def test_provenance_lists_the_source_events(self):
        _, event_ids, _ = self.build()
        self.assertEqual(sorted(event_ids), ['e1', 'e2', 'e3', 'e4', 'e5', 'e6'])

    def test_context_hash_is_stable_and_input_sensitive(self):
        text, _, _ = self.build()
        self.assertEqual(context_module.context_hash(text),
                         context_module.context_hash(text))
        self.assertNotEqual(context_module.context_hash(text),
                            context_module.context_hash(text + ' '))

    def test_nothing_outside_the_window_leaks_in(self):
        # A quest from a different play session must not appear in this
        # session's recap, or the recap describes things that did not
        # happen then.
        self.ingest([
            event('later1', 'QUEST_DETAIL', 1000 + 10 * HOUR,
                  quest={'id': 999, 'title': 'A Later Errand',
                         'description': 'Something else entirely.'}),
        ])
        session = context_module.find_play_sessions(self.conn)[0]
        text, _, _ = context_module.build_recap_context(self.conn, session)
        self.assertNotIn('A Later Errand', text)
        self.assertNotIn('Something else entirely.', text)

    def test_truncation_is_declared_rather_than_silent(self):
        text, _, stats = context_module.build_recap_context(
            self.conn, self.session, max_quests=0)
        self.assertEqual(stats['truncated_quests'], 1)
        self.assertIn('left out of this context', text)


if __name__ == '__main__':
    unittest.main()
