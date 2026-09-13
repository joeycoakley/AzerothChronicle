"""Tests for the capture added ahead of the WoW Forever beta.

The committed fixture predates all of it, so these build small synthetic
payloads instead. Each one encodes a decision that is easy to get wrong and
expensive to discover later, particularly the abandonment inference, where
being wrong silently removes a quest from the player's open threads.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'companion' / 'src'))

from azeroth_chronicle import db as db_module  # noqa: E402
from azeroth_chronicle import importer  # noqa: E402

MIGRATIONS = ROOT / 'companion' / 'migrations'
CHARACTER = {
    'guid': 'Player-1-A',
    'name': 'Tester',
    'realm': 'Realm',
    'class': 'HUNTER',
    'race': 'Skyborne',
    'faction': 'Neutral',
    'level': 5,
}


def event(event_id, event_type, timestamp, **extra):
    payload = {
        'id': event_id,
        'type': event_type,
        'timestamp': timestamp,
        'schemaVersion': 1,
        'sessionId': 's1',
        'character': CHARACTER,
        'location': {'zone': 'Mount Hyjal', 'mapId': 9000},
    }
    payload.update(extra)
    return payload


class ForeverCaptureTestCase(unittest.TestCase):
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

    def quest(self, quest_id):
        return self.conn.execute(
            'SELECT * FROM quests WHERE quest_id = ?', (quest_id,)).fetchone()


class TestCharacterIdentity(ForeverCaptureTestCase):
    def test_race_and_faction_recorded(self):
        self.ingest([event('e1', 'GOSSIP_SHOW', 100, gossip={'text': 'hello'})])
        row = self.conn.execute('SELECT * FROM characters').fetchone()
        self.assertEqual(row['race'], 'Skyborne')
        self.assertEqual(row['faction'], 'Neutral')

    def test_older_capture_without_race_does_not_erase_it(self):
        self.ingest([event('e1', 'GOSSIP_SHOW', 100, gossip={'text': 'hi'})])

        legacy = dict(CHARACTER)
        legacy.pop('race')
        legacy.pop('faction')
        importer.ingest_events(self.conn, {'character': legacy, 'events': []})
        self.conn.commit()

        row = self.conn.execute('SELECT * FROM characters').fetchone()
        self.assertEqual(row['race'], 'Skyborne',
                         'a capture predating race must not wipe a known one')


class TestQuestGreeting(ForeverCaptureTestCase):
    def test_greeting_text_becomes_dialogue(self):
        self.ingest([
            event('e1', 'QUEST_GREETING', 100,
                  source={'name': 'Elder', 'guid': 'Creature-0-1-1-1-5000-000'},
                  greeting={'text': 'Well met, traveller.',
                            'availableQuests': [{'title': 'A'}, {'title': 'B'}],
                            'activeQuests': []}),
        ])
        row = self.conn.execute(
            "SELECT * FROM dialogue WHERE dialogue_type = 'quest_greeting'").fetchone()
        self.assertIsNotNone(row, 'greeting text must be preserved as dialogue')
        self.assertEqual(row['text'], 'Well met, traveller.')

    def test_greeting_records_the_npc(self):
        self.ingest([
            event('e1', 'QUEST_GREETING', 100,
                  source={'name': 'Elder', 'guid': 'Creature-0-1-1-1-5000-000'},
                  greeting={'text': 'Well met.'}),
        ])
        row = self.conn.execute('SELECT * FROM npcs').fetchone()
        self.assertEqual(row['npc_key'], 'creature:5000')
        self.assertEqual(row['display_name'], 'Elder')


class TestAbandonment(ForeverCaptureTestCase):
    def test_removal_without_turn_in_is_abandonment(self):
        self.ingest([
            event('e1', 'QUEST_ACCEPTED', 100, quest={'id': 700}),
            event('e2', 'QUEST_REMOVED', 200, quest={'id': 700}),
        ])
        self.assertEqual(self.quest(700)['abandoned_at'], 200)

    def test_removal_after_turn_in_is_not_abandonment(self):
        # The client fires the same removal event on hand-in. Treating that
        # as an abandonment would mark every completed quest as dropped.
        self.ingest([
            event('e1', 'QUEST_ACCEPTED', 100, quest={'id': 701}),
            event('e2', 'QUEST_TURNED_IN', 200, quest={'id': 701, 'xpReward': 10}),
            event('e3', 'QUEST_REMOVED', 201, quest={'id': 701}),
        ])
        row = self.quest(701)
        self.assertIsNone(row['abandoned_at'])
        self.assertEqual(row['turned_in_at'], 200)

    def test_turn_in_arriving_after_removal_still_wins(self):
        # Event order is not guaranteed across files or sessions, and the
        # answer must not depend on which arrived first.
        self.ingest([
            event('e1', 'QUEST_REMOVED', 201, quest={'id': 702}),
            event('e2', 'QUEST_TURNED_IN', 200, quest={'id': 702}),
        ])
        self.assertIsNone(self.quest(702)['abandoned_at'])

    def test_quest_never_removed_has_no_abandonment(self):
        self.ingest([event('e1', 'QUEST_ACCEPTED', 100, quest={'id': 703})])
        self.assertIsNone(self.quest(703)['abandoned_at'])


class TestQuestLogSnapshot(ForeverCaptureTestCase):
    def test_snapshot_seeds_quests_already_in_the_log(self):
        self.ingest([
            event('e1', 'QUEST_LOG_SNAPSHOT', 100,
                  questLog={'count': 2, 'entries': [
                      {'questId': 800, 'title': 'Carried One', 'isComplete': False},
                      {'questId': 801, 'title': 'Carried Two', 'isComplete': True},
                  ]}),
        ])
        self.assertEqual(self.quest(800)['title'], 'Carried One')
        self.assertEqual(self.quest(801)['title'], 'Carried Two')

    def test_snapshot_quests_are_flagged_as_unobserved(self):
        self.ingest([
            event('e1', 'QUEST_LOG_SNAPSHOT', 100,
                  questLog={'entries': [{'questId': 802, 'title': 'Carried'}]}),
        ])
        row = self.quest(802)
        self.assertEqual(row['known_from_snapshot'], 1)
        self.assertIsNone(row['description'],
                          'nothing about this quest was actually observed')

    def test_observed_acceptance_clears_the_snapshot_flag(self):
        self.ingest([
            event('e1', 'QUEST_LOG_SNAPSHOT', 100,
                  questLog={'entries': [{'questId': 803, 'title': 'Seen'}]}),
            event('e2', 'QUEST_ACCEPTED', 200, quest={'id': 803}),
        ])
        self.assertEqual(self.quest(803)['known_from_snapshot'], 0)


class TestZoneDiscovery(ForeverCaptureTestCase):
    def test_zone_recorded_once(self):
        self.ingest([
            event('e1', 'ZONE_DISCOVERED', 100,
                  discovery={'zone': 'Zephras Isle', 'mapId': 9001, 'subZone': 'Shore'}),
        ])
        rows = self.conn.execute('SELECT * FROM zones').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['zone'], 'Zephras Isle')
        self.assertEqual(rows[0]['map_id'], 9001)

    def test_zones_are_rebuilt_not_duplicated(self):
        events = [event('e1', 'ZONE_DISCOVERED', 100,
                        discovery={'zone': 'The Riverglades', 'mapId': 9002})]
        self.ingest(events)
        importer.materialize(self.conn)
        self.conn.commit()
        rows = self.conn.execute('SELECT * FROM zones').fetchall()
        self.assertEqual(len(rows), 1, 'rematerializing must not duplicate zones')


class TestStillIdempotent(ForeverCaptureTestCase):
    def test_new_event_types_do_not_break_repeat_import(self):
        events = [
            event('e1', 'QUEST_GREETING', 100, greeting={'text': 'hello'}),
            event('e2', 'ZONE_DISCOVERED', 101, discovery={'zone': 'Hyjal'}),
            event('e3', 'QUEST_LOG_SNAPSHOT', 102,
                  questLog={'entries': [{'questId': 900, 'title': 'Q'}]}),
            event('e4', 'QUEST_REMOVED', 103, quest={'id': 900}),
        ]
        self.ingest(events)
        before = db_module.table_counts(self.conn)

        result = importer.ingest_events(
            self.conn, {'character': CHARACTER, 'events': events})
        importer.materialize(self.conn)
        self.conn.commit()

        self.assertEqual(result.events_inserted, 0)
        self.assertEqual(before, db_module.table_counts(self.conn))


if __name__ == '__main__':
    unittest.main()
