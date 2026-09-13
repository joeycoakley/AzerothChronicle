"""Assemble a spoiler-safe context for a recap.

This module is the spoiler boundary. Everything it returns is read from the
player's own captured history, and nothing else ever reaches the model. If
a fact is not in here, the model has no way to learn it, which is what makes
the promise enforceable rather than aspirational.

It is also the reason `recap --dry-run` exists: the exact text that would be
sent can be printed and read first. A privacy claim you cannot inspect is
not worth much.

A note on what a session means. The addon records one session per reload,
which is far too granular to recap: a single evening produces a dozen. What
a player means by "this session" is a stretch of play, so activity is
grouped by gaps in the timeline instead.
"""
import hashlib
import json

# Two hours of no recorded activity ends a play session. Long enough to
# survive a break, short enough that yesterday evening and this morning do
# not merge into one recap.
DEFAULT_GAP_SECONDS = 2 * 60 * 60

DIALOGUE_LABELS = {
    'gossip': 'said',
    'quest_greeting': 'greeted you',
    'quest_progress': 'said when you returned',
    'quest_completion': 'said when you finished',
    'say': 'said aloud',
    'yell': 'shouted',
    'emote': 'did',
    'whispers': 'whispered',
}


class PlaySession:
    def __init__(self, start, end, event_ids):
        self.start = start
        self.end = end
        self.event_ids = event_ids

    @property
    def event_count(self):
        return len(self.event_ids)

    def __repr__(self):
        return '<PlaySession %s-%s, %d events>' % (self.start, self.end, self.event_count)


def find_play_sessions(conn, gap_seconds=DEFAULT_GAP_SECONDS):
    """Group recorded events into stretches of play, newest last."""
    rows = conn.execute(
        'SELECT event_id, timestamp FROM events'
        ' WHERE timestamp IS NOT NULL ORDER BY timestamp'
    ).fetchall()

    sessions = []
    current_ids, start, previous = [], None, None

    for row in rows:
        timestamp = row['timestamp']
        if previous is not None and (timestamp - previous) > gap_seconds:
            sessions.append(PlaySession(start, previous, current_ids))
            current_ids, start = [], timestamp
        if start is None:
            start = timestamp
        current_ids.append(row['event_id'])
        previous = timestamp

    if current_ids:
        sessions.append(PlaySession(start, previous, current_ids))

    return sessions


def find_closed_sessions(conn, gap_seconds=DEFAULT_GAP_SECONDS, now=None):
    """Sessions safe to recap: ones that cannot grow any more.

    Exists for the watcher, which reacts to a file changing rather than to
    the player explicitly saying "I'm done." A session is closed once any
    of the following holds:

      - a later session already exists, so nothing more can be appended to
        it;
      - enough wall-clock time has passed since its last event that a
        further reload adding to it is no longer plausible - the same gap
        rule that splits sessions in the first place, just measured against
        the clock instead of only against events that already happened;
      - the player explicitly asked for a recap (the in-game "Request
        recap" button) at or after this session started. That is a direct
        request, not a heuristic, and overrides the other two: someone who
        just turned in quests and wants a recap right now should not have
        to wait out the gap window first.

    Without the wall-clock condition, the most recent session would never
    be recapped until the player did something else entirely, which for
    someone who plays once and logs off for the night is never.
    """
    import time as time_module

    now = now if now is not None else time_module.time()
    sessions = find_play_sessions(conn, gap_seconds)
    if not sessions:
        return []

    closed = sessions[:-1]
    last = sessions[-1]

    row = conn.execute(
        'SELECT MAX(recap_requested_at) AS requested_at FROM characters').fetchone()
    requested_at = row['requested_at'] if row else None

    if last.end is not None and (now - last.end) > gap_seconds:
        closed.append(last)
    elif (requested_at is not None and last.start is not None
          and requested_at >= last.start):
        closed.append(last)

    return closed


def _quests_in_window(conn, start, end):
    """Quests the player touched during the window, with their full text.

    Selected by activity in the window, but the text is the whole quest,
    because a completion line makes no sense without the request that
    prompted it.
    """
    rows = conn.execute(
        'SELECT DISTINCT q.* FROM quests q'
        ' JOIN events e ON e.character_id = q.character_id'
        " WHERE e.timestamp BETWEEN ? AND ?"
        "   AND json_extract(e.raw_payload_json, '$.quest.id') = q.quest_id"
        ' ORDER BY COALESCE(q.turned_in_at, q.accepted_at, q.first_seen_at)',
        (start, end)).fetchall()
    return rows


def _dialogue_for_quest(conn, quest_id, start, end):
    return conn.execute(
        'SELECT dialogue_type, text, timestamp FROM dialogue'
        ' WHERE quest_id = ? AND timestamp BETWEEN ? AND ?'
        ' ORDER BY timestamp', (quest_id, start, end)).fetchall()


def _npcs_in_window(conn, start, end):
    return conn.execute(
        'SELECT DISTINCT n.npc_key, n.display_name, n.first_zone, n.first_seen_at'
        ' FROM npcs n JOIN dialogue d ON d.npc_key = n.npc_key'
        ' WHERE d.timestamp BETWEEN ? AND ?'
        ' ORDER BY n.first_seen_at', (start, end)).fetchall()


def _format_money(copper):
    if not copper:
        return None
    gold, remainder = divmod(int(copper), 10000)
    silver, copper_left = divmod(remainder, 100)
    parts = []
    if gold:
        parts.append('%dg' % gold)
    if silver:
        parts.append('%ds' % silver)
    if copper_left:
        parts.append('%dc' % copper_left)
    return ' '.join(parts)


def build_recap_context(conn, session, max_quests=None):
    """Render one play session as text for the model.

    Returns (context_text, source_event_ids, stats). Nothing is silently
    dropped: if a cap is applied, the text says so, because a recap that
    quietly omits half the session is worse than one that admits it.
    """
    start, end = session.start, session.end

    character = conn.execute('SELECT * FROM characters LIMIT 1').fetchone()
    quests = _quests_in_window(conn, start, end)
    npcs = _npcs_in_window(conn, start, end)

    truncated = 0
    if max_quests is not None and len(quests) > max_quests:
        truncated = len(quests) - max_quests
        quests = quests[:max_quests]

    lines = []
    add = lines.append

    add('PLAYER CHARACTER')
    if character:
        descriptor = ' '.join(
            str(part) for part in (character['race'], character['class']) if part)
        add('  %s, a %s' % (character['name'], descriptor or 'adventurer'))
        if character['faction']:
            add('  Faction: %s' % character['faction'])
        add('  Level: %s' % character['level_last_seen'])
    add('')

    zones = conn.execute(
        'SELECT DISTINCT zone FROM events'
        ' WHERE timestamp BETWEEN ? AND ? AND zone IS NOT NULL',
        (start, end)).fetchall()
    if zones:
        add('WHERE THIS TOOK PLACE')
        for row in zones:
            add('  %s' % row['zone'])
        add('')

    discovered = conn.execute(
        'SELECT zone FROM zones WHERE discovered_at BETWEEN ? AND ?',
        (start, end)).fetchall()
    if discovered:
        add('ARRIVED FOR THE FIRST TIME')
        for row in discovered:
            add('  %s' % row['zone'])
        add('')

    add('QUESTS')
    if not quests:
        add('  (none recorded in this stretch of play)')

    for quest in quests:
        add('')
        add('  --- %s ---' % (quest['title'] or 'Quest %s' % quest['quest_id']))

        state = 'turned in' if quest['turned_in_at'] else (
            'abandoned' if quest['abandoned_at'] else 'still in progress')
        add('  Status: %s' % state)

        if quest['giver_npc_key']:
            giver = conn.execute(
                'SELECT display_name FROM npcs WHERE npc_key = ?',
                (quest['giver_npc_key'],)).fetchone()
            if giver and giver['display_name']:
                add('  Given by: %s' % giver['display_name'])

        if quest['known_from_snapshot']:
            add('  Note: this quest was already being carried before recording'
                ' began, so how it started was never observed.')

        if quest['description']:
            add('  What the character was told:')
            add('    ' + quest['description'].replace('\n', '\n    '))
        if quest['objectives_text']:
            add('  The task: %s' % quest['objectives_text'])

        # Progress dialogue repeats every time the player checks in, and the
        # completion text is stored both as a quest field and as a dialogue
        # line. Sending the same sentence three times teaches the model
        # nothing and costs tokens, so each distinct line appears once.
        seen_text = set()
        for line in _dialogue_for_quest(conn, quest['quest_id'], start, end):
            body = (line['text'] or '').strip()
            if not body or body in seen_text:
                continue
            seen_text.add(body)
            label = DIALOGUE_LABELS.get(line['dialogue_type'], line['dialogue_type'])
            add('  What was %s: %s' % (label, body))

        completion = (quest['completion_text'] or '').strip()
        if completion and completion not in seen_text:
            add('  On completion: %s' % completion)

        rewards = []
        if quest['xp_reward']:
            rewards.append('%s experience' % quest['xp_reward'])
        money = _format_money(quest['money_reward'])
        if money:
            rewards.append(money)
        if rewards:
            add('  Reward: %s' % ', '.join(rewards))

    if truncated:
        add('')
        add('  (%d further quests in this session were left out of this context'
            ' to keep it manageable; the recap covers the rest)' % truncated)

    if npcs:
        add('')
        add('CHARACTERS ENCOUNTERED')
        for npc in npcs:
            where = ' in %s' % npc['first_zone'] if npc['first_zone'] else ''
            add('  %s%s' % (npc['display_name'] or npc['npc_key'], where))

    levels = conn.execute(
        "SELECT raw_payload_json, zone FROM events"
        " WHERE event_type = 'PLAYER_LEVEL_UP' AND timestamp BETWEEN ? AND ?",
        (start, end)).fetchall()
    if levels:
        add('')
        add('MILESTONES')
        for row in levels:
            payload = json.loads(row['raw_payload_json'])
            level = (payload.get('progression') or {}).get('level')
            add('  Reached level %s%s'
                % (level, ' in %s' % row['zone'] if row['zone'] else ''))

    text = '\n'.join(lines)
    stats = {
        'quests': len(quests),
        'npcs': len(npcs),
        'events': session.event_count,
        'truncated_quests': truncated,
    }
    return text, list(session.event_ids), stats


def context_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
