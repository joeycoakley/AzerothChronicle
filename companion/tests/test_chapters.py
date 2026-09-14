"""Tests for chapters.py.

The property under test is not "does it call the model correctly" (recap.py
already proves that pattern works) - it's the thing that makes a chapter a
different kind of object from a session recap: one stable row per zone that
*updates in place* as more content accumulates, rather than a new row per
distinct piece of content. Get this wrong and either revisiting a zone spawns
duplicate "chapters," or a chapter never updates once written.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'companion' / 'src'))

from azeroth_chronicle import chapters as chapters_module  # noqa: E402
from azeroth_chronicle import db as db_module  # noqa: E402
from azeroth_chronicle import importer  # noqa: E402
from azeroth_chronicle import llm as llm_module  # noqa: E402

MIGRATIONS = ROOT / 'companion' / 'migrations'
CHARACTER = {'guid': 'Player-1-A', 'name': 'Tester', 'realm': 'Realm', 'class': 'HUNTER'}


def event(event_id, event_type, timestamp, zone='Teldrassil', **extra):
    payload = {
        'id': event_id, 'type': event_type, 'timestamp': timestamp,
        'schemaVersion': 1, 'sessionId': 's1', 'character': CHARACTER,
        'location': {'zone': zone},
    }
    payload.update(extra)
    return payload


def fake_summarize(text, **kwargs):
    return {'text': 'chapter text v1: %d quest(s)' % text.count('--- '),
            'model': 'fake-model', 'input_tokens': 10, 'output_tokens': 5}


class ChapterTestCase(unittest.TestCase):
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

    def summary_row(self, zone):
        return self.conn.execute(
            'SELECT * FROM summaries WHERE summary_id = ?',
            ('chapter-%s' % chapters_module.zone_slug(zone),)).fetchone()


class TestZoneSlug(unittest.TestCase):
    def test_readable_and_stable(self):
        self.assertEqual(chapters_module.zone_slug('Teldrassil'), 'teldrassil')

    def test_punctuation_and_case_normalized(self):
        self.assertEqual(chapters_module.zone_slug("Un'Goro Crater"), 'un-goro-crater')

    def test_never_empty(self):
        self.assertTrue(chapters_module.zone_slug('!!!'))


class TestGenerateAndSaveChapter(ChapterTestCase):
    def setUp(self):
        super().setUp()
        self.ingest([
            event('e1', 'QUEST_DETAIL', 1000,
                  quest={'id': 1, 'title': 'First Quest', 'description': 'Do a thing.'}),
        ])

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_first_write_creates_one_row(self, mock_summarize):
        outcome = chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')
        self.assertFalse(outcome.cached)
        row = self.summary_row('Teldrassil')
        self.assertIsNotNone(row)
        self.assertEqual(row['kind'], 'zone_chapter')
        self.assertEqual(row['zone'], 'Teldrassil')

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_unchanged_zone_is_a_cache_hit(self, mock_summarize):
        chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')
        outcome = chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')
        self.assertTrue(outcome.cached)
        mock_summarize.assert_called_once()

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_more_content_updates_the_same_row_not_a_new_one(self, mock_summarize):
        """The core property: this is one evolving chapter, not a growing
        pile of versions."""
        chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')

        self.ingest([
            event('e2', 'QUEST_DETAIL', 2000,
                  quest={'id': 2, 'title': 'Second Quest', 'description': 'Do another.'}),
        ])
        outcome = chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')

        self.assertFalse(outcome.cached, 'new content must trigger a real regeneration')
        self.assertEqual(mock_summarize.call_count, 2)

        rows = self.conn.execute(
            "SELECT COUNT(*) AS n FROM summaries WHERE zone = 'Teldrassil'").fetchone()
        self.assertEqual(rows['n'], 1, 'must still be exactly one row for this zone')

        row = self.summary_row('Teldrassil')
        self.assertIn('2 quest', row['text'], 'the row must hold the updated text')

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_force_regenerates_even_when_unchanged(self, mock_summarize):
        chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')
        outcome = chapters_module.generate_and_save_chapter(
            self.conn, 'Teldrassil', force=True)
        self.assertFalse(outcome.cached)
        self.assertEqual(mock_summarize.call_count, 2)

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_two_different_zones_get_two_different_rows(self, mock_summarize):
        self.ingest([event('e2', 'QUEST_DETAIL', 3000, zone='Westfall',
                          quest={'id': 3, 'title': 'Elsewhere', 'description': 'x'})])

        chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')
        chapters_module.generate_and_save_chapter(self.conn, 'Westfall')

        self.assertIsNotNone(self.summary_row('Teldrassil'))
        self.assertIsNotNone(self.summary_row('Westfall'))
        total = self.conn.execute(
            "SELECT COUNT(*) AS n FROM summaries WHERE kind = 'zone_chapter'").fetchone()
        self.assertEqual(total['n'], 2)

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_no_save_does_not_persist(self, mock_summarize):
        chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil', save=False)
        self.assertIsNone(self.summary_row('Teldrassil'))

    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_chapter_uses_the_chapter_prompt_not_the_recap_prompt(self, mock_summarize):
        chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')
        _, kwargs = mock_summarize.call_args
        self.assertEqual(kwargs['system_prompt'], llm_module.CHAPTER_SYSTEM_PROMPT)

    def test_llm_failure_propagates(self):
        with mock.patch.object(llm_module, 'summarize',
                               side_effect=llm_module.LlmUnavailable('down')):
            with self.assertRaises(llm_module.LlmUnavailable):
                chapters_module.generate_and_save_chapter(self.conn, 'Teldrassil')


class TestZonesInDiscoveryOrder(ChapterTestCase):
    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_ordered_by_first_arrival_not_alphabetically(self, mock_summarize):
        self.ingest([
            event('e1', 'ZONE_DISCOVERED', 5000, zone='Westfall',
                  discovery={'zone': 'Westfall'}),
            event('e2', 'ZONE_DISCOVERED', 1000, zone='Teldrassil',
                  discovery={'zone': 'Teldrassil'}),
        ])
        zones = chapters_module.zones_in_discovery_order(self.conn)
        self.assertEqual(zones, ['Teldrassil', 'Westfall'],
                         'earlier discovered_at must sort first, not alphabetical order')


class TestGenerateAllChapters(ChapterTestCase):
    @mock.patch.object(llm_module, 'summarize', side_effect=fake_summarize)
    def test_writes_a_chapter_per_zone_and_skips_unchanged_on_rerun(self, mock_summarize):
        # Real captures always pair a zone with a ZONE_DISCOVERED-equivalent
        # event too - it fires on every login, not just a genuine first
        # arrival - so the zones table is never empty for a zone with real
        # quest activity in it. See the real-database check that motivated
        # keying chapters off the zones table rather than raw event zones:
        # it also naturally excludes the "Unknown" zone value that shows up
        # when position capture briefly fails.
        self.ingest([
            event('e1', 'QUEST_DETAIL', 1000, zone='Teldrassil',
                  quest={'id': 1, 'title': 'A', 'description': 'x'}),
            event('e1z', 'ZONE_DISCOVERED', 1000, zone='Teldrassil',
                  discovery={'zone': 'Teldrassil'}),
            event('e2', 'QUEST_DETAIL', 2000, zone='Westfall',
                  quest={'id': 2, 'title': 'B', 'description': 'y'}),
            event('e2z', 'ZONE_DISCOVERED', 2000, zone='Westfall',
                  discovery={'zone': 'Westfall'}),
        ])

        first = chapters_module.generate_all_chapters(self.conn)
        self.assertEqual(len(first), 2)
        self.assertTrue(all(not o.cached for o in first))
        self.assertEqual(mock_summarize.call_count, 2)

        second = chapters_module.generate_all_chapters(self.conn)
        self.assertTrue(all(o.cached for o in second),
                        'a rerun with no new content must not call the model again')
        self.assertEqual(mock_summarize.call_count, 2, 'still just the original two calls')


if __name__ == '__main__':
    unittest.main()
