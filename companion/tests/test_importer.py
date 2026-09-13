"""Tests for the importer, run with unittest so nothing needs installing.

    python -m unittest discover -s companion/tests

The first three tests are the Milestone 4 acceptance criteria stated as
code: import is idempotent, a deleted database rebuilds identically, and
derived tables are disposable.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'companion' / 'src'))

from azeroth_chronicle import db as db_module  # noqa: E402
from azeroth_chronicle import importer, luaparse  # noqa: E402

FIXTURE = ROOT / 'samples' / 'savedvariables' / 'shadowglen-quest-456.lua'
MIGRATIONS = ROOT / 'companion' / 'migrations'


class ImporterTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'test.sqlite3'
        self.conn = db_module.open_database(self.db_path, MIGRATIONS)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def counts(self, conn=None):
        return db_module.table_counts(conn or self.conn)


class TestIdempotency(ImporterTestCase):
    def test_repeat_import_inserts_nothing_new(self):
        first = importer.import_file(self.conn, FIXTURE)
        self.assertGreater(first.events_inserted, 0, 'first import should insert events')
        self.assertEqual(first.events_skipped, 0)

        after_first = self.counts()

        second = importer.import_file(self.conn, FIXTURE)
        self.assertEqual(second.events_inserted, 0, 'reimport must insert nothing')
        self.assertEqual(second.events_skipped, second.events_seen)

        self.assertEqual(after_first, self.counts(),
                         'row counts must be identical after reimporting')

    def test_third_import_still_stable(self):
        importer.import_file(self.conn, FIXTURE)
        importer.import_file(self.conn, FIXTURE)
        snapshot = self.counts()
        importer.import_file(self.conn, FIXTURE)
        self.assertEqual(snapshot, self.counts())


class TestRebuildable(ImporterTestCase):
    def test_deleted_database_rebuilds_identically(self):
        importer.import_file(self.conn, FIXTURE)
        original = self.counts()
        original_quests = self.conn.execute(
            'SELECT * FROM quests ORDER BY quest_id').fetchall()
        self.conn.close()

        self.db_path.unlink()
        for suffix in ('-wal', '-shm'):
            extra = Path(str(self.db_path) + suffix)
            if extra.exists():
                extra.unlink()

        conn = db_module.open_database(self.db_path, MIGRATIONS)
        importer.import_file(conn, FIXTURE)

        self.assertEqual(original, self.counts(conn))
        rebuilt_quests = conn.execute('SELECT * FROM quests ORDER BY quest_id').fetchall()
        self.assertEqual([tuple(r) for r in original_quests],
                         [tuple(r) for r in rebuilt_quests])
        conn.close()
        self.conn = db_module.open_database(self.db_path, MIGRATIONS)

    def test_derived_tables_are_disposable(self):
        importer.import_file(self.conn, FIXTURE)
        before = self.counts()

        db_module.clear_derived(self.conn)
        cleared = self.counts()
        for table in db_module.DERIVED_TABLES:
            self.assertEqual(cleared[table], 0)
        self.assertEqual(cleared['events'], before['events'],
                         'clearing derived data must not touch raw events')

        importer.materialize(self.conn)
        self.conn.commit()
        self.assertEqual(before, self.counts())


class TestMaterialization(ImporterTestCase):
    def setUp(self):
        super().setUp()
        importer.import_file(self.conn, FIXTURE)

    def test_quest_456_full_lifecycle(self):
        row = self.conn.execute(
            'SELECT * FROM quests WHERE quest_id = 456').fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['title'], 'The Balance of Nature')
        self.assertIsNotNone(row['accepted_at'])
        self.assertIsNotNone(row['completed_at'])
        self.assertIsNotNone(row['turned_in_at'])
        self.assertEqual(row['xp_reward'], 170)
        self.assertEqual(row['money_reward'], 35)
        self.assertIn('balance of nature', (row['description'] or '').lower())
        self.assertTrue(row['objectives_text'])
        self.assertTrue(row['completion_text'])

    def test_second_quest_present(self):
        row = self.conn.execute(
            'SELECT * FROM quests WHERE quest_id = 458').fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['title'], 'The Woodland Protector')
        # Seen but never accepted, so the lifecycle columns stay empty
        # rather than being filled in with guesses.
        self.assertIsNone(row['turned_in_at'])

    def test_quest_giver_linked_to_npc(self):
        row = self.conn.execute(
            'SELECT n.display_name FROM quests q'
            ' JOIN npcs n ON n.npc_key = q.giver_npc_key'
            ' WHERE q.quest_id = 456').fetchone()
        self.assertIsNotNone(row, 'quest 456 should link to its giver')
        self.assertEqual(row['display_name'], 'Conservator Ilthalaine')

    def test_npc_key_is_creature_id_not_spawn(self):
        keys = [r['npc_key'] for r in self.conn.execute('SELECT npc_key FROM npcs')]
        self.assertIn('creature:2079', keys)
        for key in keys:
            self.assertNotIn('00002018B8', key,
                             'spawn-specific GUID must not become the identity')

    def test_dialogue_captured_from_several_sources(self):
        types = {r['dialogue_type'] for r in self.conn.execute(
            'SELECT DISTINCT dialogue_type FROM dialogue')}
        self.assertIn('gossip', types)
        self.assertIn('quest_progress', types)
        self.assertIn('quest_completion', types)

    def test_events_keep_their_raw_payload(self):
        row = self.conn.execute(
            "SELECT raw_payload_json FROM events WHERE event_type = 'QUEST_DETAIL' LIMIT 1"
        ).fetchone()
        payload = json.loads(row['raw_payload_json'])
        self.assertIn('quest', payload)
        self.assertIn('description', payload['quest'])

    def test_position_recorded(self):
        row = self.conn.execute(
            'SELECT zone, subzone, map_id, x, y FROM events'
            ' WHERE map_id IS NOT NULL LIMIT 1').fetchone()
        self.assertIsNotNone(row, 'at least one event should carry a map id')
        self.assertEqual(row['map_id'], 1438)
        self.assertIsNotNone(row['x'])

    def test_sessions_imported_including_empty_ones(self):
        # Reloads produce sessions with no events. They are legitimate
        # history, not errors, and must survive the import.
        count = self.conn.execute('SELECT COUNT(*) AS n FROM sessions').fetchone()['n']
        self.assertGreater(count, 1)


class TestMalformedInput(ImporterTestCase):
    def test_event_without_id_is_skipped_not_fatal(self):
        payload = {
            'character': {'guid': 'Player-1-1', 'name': 'X', 'realm': 'R'},
            'events': [
                {'id': 'good-1', 'type': 'GOSSIP_SHOW', 'timestamp': 1,
                 'gossip': {'text': 'hello'}},
                {'type': 'GOSSIP_SHOW', 'timestamp': 2},          # no id
                'not a table',                                     # wrong type
            ],
        }
        result = importer.ingest_events(self.conn, payload)
        self.conn.commit()

        self.assertEqual(result.events_inserted, 1)
        self.assertEqual(len(result.malformed), 2)

    def test_missing_saved_variable_is_reported(self):
        path = Path(self.tmp.name) / 'wrong.lua'
        path.write_text('SomeOtherAddonDB = { }', encoding='utf-8')
        with self.assertRaises(ValueError):
            importer.import_file(self.conn, path)


class TestLuaParser(unittest.TestCase):
    def test_strings_numbers_booleans_nil(self):
        data = luaparse.loads(
            'X = { ["s"] = "a\\nb", ["i"] = 42, ["f"] = -1.5, '
            '["t"] = true, ["f2"] = false, ["n"] = nil, }')
        self.assertEqual(data['X']['s'], 'a\nb')
        self.assertEqual(data['X']['i'], 42)
        self.assertEqual(data['X']['f'], -1.5)
        self.assertIs(data['X']['t'], True)
        self.assertIs(data['X']['f2'], False)
        self.assertIsNone(data['X']['n'])

    def test_array_tables(self):
        data = luaparse.loads('X = { 1, 2, 3, }')
        self.assertEqual(data['X'], [1, 2, 3])

    def test_nested_and_comments(self):
        data = luaparse.loads(
            '-- a header comment\nX = { ["a"] = { ["b"] = { 1, 2 } } }')
        self.assertEqual(data['X']['a']['b'], [1, 2])

    def test_quotes_inside_strings(self):
        data = luaparse.loads(r'X = { ["s"] = "he said \"go\" now" }')
        self.assertEqual(data['X']['s'], 'he said "go" now')

    def test_rejects_garbage_rather_than_guessing(self):
        with self.assertRaises(luaparse.LuaParseError):
            luaparse.loads('X = { ["a"] = @@@ }')

    def test_real_fixture_parses(self):
        data = luaparse.load_file(FIXTURE)
        payload = data['AzerothChronicleDB']
        self.assertEqual(payload['schemaVersion'], 1)
        self.assertEqual(len(payload['events']), 11)


class TestNpcKey(unittest.TestCase):
    def test_creature_guid_reduces_to_creature_id(self):
        key = importer.npc_key_for(
            {'guid': 'Creature-0-5165-1-35-2079-00002018B8', 'name': 'Someone'})
        self.assertEqual(key, 'creature:2079')

    def test_two_spawns_of_same_creature_share_a_key(self):
        a = importer.npc_key_for({'guid': 'Creature-0-5165-1-35-2079-00002018B8'})
        b = importer.npc_key_for({'guid': 'Creature-0-4210-0-11-2079-0000FFFFFF'})
        self.assertEqual(a, b)

    def test_falls_back_to_name_without_guid(self):
        self.assertEqual(importer.npc_key_for({'name': 'Nameless'}), 'name:Nameless')

    def test_returns_none_for_empty_source(self):
        self.assertIsNone(importer.npc_key_for({}))
        self.assertIsNone(importer.npc_key_for(None))


if __name__ == '__main__':
    unittest.main()
