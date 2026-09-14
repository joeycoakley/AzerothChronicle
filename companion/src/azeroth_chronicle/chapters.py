"""Generate and persist one chapter of the character's ongoing chronicle.

Deliberately not a variant of recap.py, despite the strong family
resemblance, because chapters and session recaps answer different
questions about identity:

A session recap describes a closed, immutable slice of time. Two
different sessions always produce two different rows, forever - that is
exactly right, since nothing should ever cause session A's recap to be
overwritten by session B's.

A chapter describes an open-ended place. The player will very likely
return to the same zone weeks later and do more there, and when that
happens the *same* chapter should grow to reflect it, not sit stale next
to a second, competing "Teldrassil" entry. So a chapter's identity is the
zone itself, stable forever, while its content and stored hash update in
place whenever there is something new to say. Content-hash comparison
still decides whether the model needs to run at all - a fresh pass over a
zone with nothing new is a cache hit, same as a session recap.
"""
import json
import re
import time

from . import context as context_module
from . import llm as llm_module

# A chapter's context can span far more quests than one session ever would
# (a whole zone across an entire leveling arc), so it gets more room to
# read and more room to answer in than a session recap - see llm.py for
# why this stays well under the model's trained maximum regardless.
CHAPTER_NUM_CTX = 8192
CHAPTER_MAX_OUTPUT_TOKENS = 1200

_SLUG_UNSAFE = re.compile(r'[^a-z0-9]+')


def zone_slug(zone):
    """A stable id fragment for a zone name, for the summary_id column.

    Not shown to the player anywhere; only exists so two zones with
    different display text (case, punctuation) cannot collide, and so the
    stored id is at least readable in a database browser while debugging.
    """
    return _SLUG_UNSAFE.sub('-', zone.lower()).strip('-') or 'zone'


class ChapterOutcome:
    def __init__(self, text, model, cached, stats, digest):
        self.text = text
        self.model = model
        self.cached = cached
        self.stats = stats
        self.digest = digest


def generate_and_save_chapter(conn, zone, character_id=None, max_quests=None,
                              force=False, save=True, on_will_generate=None):
    """Build the context for one zone and get its chapter.

    Returns cached text without touching the model if the zone's content
    has not changed since it was last written, unless `force` is set.
    Raises `llm.LlmUnavailable` if generation is needed and the local model
    cannot be reached.
    """
    text, stats = context_module.build_chapter_context(conn, zone, max_quests=max_quests)
    digest = context_module.context_hash(text)

    summary_id = 'chapter-%s' % zone_slug(zone)
    existing = conn.execute(
        'SELECT text, model, context_hash FROM summaries WHERE summary_id = ?',
        (summary_id,)).fetchone()

    if existing and existing['context_hash'] == digest and not force:
        return ChapterOutcome(existing['text'], existing['model'], True, stats, digest)

    if on_will_generate:
        on_will_generate()

    result = llm_module.summarize(
        text, system_prompt=llm_module.CHAPTER_SYSTEM_PROMPT,
        instruction='Write this chapter of the chronicle.',
        max_output_tokens=CHAPTER_MAX_OUTPUT_TOKENS, num_ctx=CHAPTER_NUM_CTX)

    if save:
        if character_id is None:
            row = conn.execute('SELECT character_id FROM characters LIMIT 1').fetchone()
            character_id = row['character_id'] if row else None

        bounds = conn.execute(
            'SELECT MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts'
            ' FROM events WHERE zone = ?', (zone,)).fetchone()

        conn.execute(
            'INSERT INTO summaries ('
            ' summary_id, character_id, kind, window_start, window_end, model,'
            ' created_at, context_hash, text, zone'
            ') VALUES (?,?,?,?,?,?,?,?,?,?)'
            ' ON CONFLICT(summary_id) DO UPDATE SET'
            '   window_start = excluded.window_start,'
            '   window_end = excluded.window_end,'
            '   model = excluded.model,'
            '   created_at = excluded.created_at,'
            '   context_hash = excluded.context_hash,'
            '   text = excluded.text',
            (summary_id, character_id, 'zone_chapter',
             bounds['first_ts'] if bounds else None,
             bounds['last_ts'] if bounds else None,
             result['model'], int(time.time()), digest, result['text'], zone))
        conn.commit()

    return ChapterOutcome(result['text'], result['model'], False, stats, digest)


def zones_in_discovery_order(conn):
    """Every zone the character has visited, oldest arrival first - the
    natural chapter order for "starting from the beginning."""
    rows = conn.execute(
        'SELECT zone FROM zones WHERE zone IS NOT NULL'
        ' ORDER BY COALESCE(discovered_at, 0)').fetchall()
    return [row['zone'] for row in rows]


def generate_all_chapters(conn, max_quests=None, force=False, on_zone_start=None):
    """Update the chapter for every zone the character has ever visited.

    Zones with nothing new since their last chapter was written are cache
    hits and cost nothing beyond a context rebuild; only genuinely changed
    zones spend model time. Returns the list of ChapterOutcome, one per
    zone, in the same discovery order.
    """
    outcomes = []
    for zone in zones_in_discovery_order(conn):
        if on_zone_start:
            on_zone_start(zone)
        outcomes.append(generate_and_save_chapter(
            conn, zone, max_quests=max_quests, force=force))
    return outcomes
