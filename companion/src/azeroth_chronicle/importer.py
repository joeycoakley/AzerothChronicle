"""Ingest SavedVariables into SQLite, then materialize derived entities.

Two phases, kept separate on purpose.

Ingest inserts raw events keyed by their addon-assigned event id. That id
is stable and unique by construction, so re-importing the same file
inserts nothing new. This is the guarantee Milestone 4 asks for.

Materialize rebuilds quests, NPCs, dialogue and item text from the events
table. It runs over every event, not just newly inserted ones, and clears
the derived tables first. That is slower than incremental updates and
deliberately so: it means the derived state is a pure function of the
events, and can never drift from it after a partial or repeated import.
"""
import json
import re
import time

from . import db as db_module
from . import luaparse

SAVED_VARIABLE_NAME = 'AzerothChronicleDB'

QUEST_EVENTS = {
    'QUEST_DETAIL', 'QUEST_ACCEPTED', 'QUEST_PROGRESS',
    'QUEST_COMPLETE', 'QUEST_TURNED_IN', 'QUEST_REMOVED',
}

DIALOGUE_EVENTS = {
    'CHAT_MSG_MONSTER_SAY', 'CHAT_MSG_MONSTER_YELL',
    'CHAT_MSG_MONSTER_EMOTE', 'CHAT_MSG_MONSTER_WHISPER',
}

# Creature-0-5165-1-35-2079-00002018B8
#                          ^^^^ the creature id, stable across spawns
_CREATURE_GUID = re.compile(
    r'^(?:Creature|Vehicle|Pet|GameObject)-\d+-\d+-\d+-\d+-(\d+)-', re.IGNORECASE)


class ImportResult:
    def __init__(self, source):
        self.source = str(source)
        self.events_seen = 0
        self.events_inserted = 0
        self.events_skipped = 0
        self.malformed = []

    def __repr__(self):
        return ('<ImportResult %s seen=%d inserted=%d skipped=%d malformed=%d>'
                % (self.source, self.events_seen, self.events_inserted,
                   self.events_skipped, len(self.malformed)))


def npc_key_for(source):
    """Stable identity for an NPC.

    The full GUID encodes a particular spawn, so two sightings of the same
    character produce different GUIDs. The creature id embedded in it is
    the part that actually identifies who they are. Display name is the
    fallback, and a poor one: names are not unique and are localized.
    """
    if not isinstance(source, dict):
        return None
    guid = source.get('guid')
    if isinstance(guid, str):
        match = _CREATURE_GUID.match(guid)
        if match:
            return 'creature:%s' % match.group(1)
        if guid:
            return 'guid:%s' % guid
    name = source.get('name')
    if name:
        return 'name:%s' % name
    return None


def _as_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _as_float(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# -- phase one: ingest ---------------------------------------------------

def ingest_events(conn, payload, source='<memory>', imported_at=None):
    """Insert events that are not already present, by event id."""
    result = ImportResult(source)
    imported_at = imported_at or int(time.time())

    character = payload.get('character') or {}
    character_id = character.get('guid')

    _upsert_character(conn, character)
    _upsert_sessions(conn, payload.get('sessions') or {}, character_id)

    events = payload.get('events') or []
    if isinstance(events, dict):
        # A table with holes deserializes as a dict; take values in key
        # order so an unusual file still imports rather than failing.
        events = [events[k] for k in sorted(events.keys(), key=str)]

    for raw in events:
        result.events_seen += 1

        if not isinstance(raw, dict):
            result.malformed.append('event %d is not a table' % result.events_seen)
            continue

        event_id = raw.get('id')
        if not event_id:
            # Skip rather than synthesize an id: a fabricated key would
            # duplicate on the next import, which is the exact failure
            # this importer exists to avoid.
            result.malformed.append('event %d has no id' % result.events_seen)
            continue

        location = raw.get('location') or {}
        event_character = raw.get('character') or {}

        row = (
            event_id,
            _as_int(raw.get('schemaVersion')),
            raw.get('sessionId'),
            event_character.get('guid') or character_id,
            raw.get('type'),
            _as_int(raw.get('timestamp')),
            location.get('zone'),
            location.get('subZone'),
            _as_int(location.get('mapId')),
            _as_float(location.get('x')),
            _as_float(location.get('y')),
            json.dumps(raw, sort_keys=True, ensure_ascii=False),
            imported_at,
        )

        cursor = conn.execute(
            'INSERT OR IGNORE INTO events ('
            ' event_id, schema_version, session_id, character_id, event_type,'
            ' timestamp, zone, subzone, map_id, x, y, raw_payload_json, imported_at'
            ') VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', row)

        if cursor.rowcount:
            result.events_inserted += 1
        else:
            result.events_skipped += 1

    return result


def _upsert_character(conn, character):
    guid = character.get('guid')
    if not guid:
        return
    conn.execute(
        'INSERT INTO characters ('
        ' character_id, name, realm, class, race, faction, level_last_seen, first_seen_at'
        ') VALUES (?,?,?,?,?,?,?,?)'
        ' ON CONFLICT(character_id) DO UPDATE SET'
        '   name = excluded.name,'
        '   realm = excluded.realm,'
        '   class = excluded.class,'
        # An older capture predating race and faction carries NULL for them.
        # COALESCE keeps what is already known instead of erasing it.
        '   race = COALESCE(excluded.race, characters.race),'
        '   faction = COALESCE(excluded.faction, characters.faction),'
        # Level only ever moves up, and an older file must not walk it back.
        '   level_last_seen = MAX(COALESCE(characters.level_last_seen, 0),'
        '                         COALESCE(excluded.level_last_seen, 0))',
        (guid, character.get('name'), character.get('realm'), character.get('class'),
         character.get('race'), character.get('faction'),
         _as_int(character.get('level')), int(time.time())))


def _upsert_sessions(conn, sessions, character_id):
    if isinstance(sessions, list):
        sessions = {s.get('id'): s for s in sessions if isinstance(s, dict)}

    for session_id, session in sessions.items():
        if not isinstance(session, dict):
            continue
        missing = session.get('missingApis')
        conn.execute(
            'INSERT INTO sessions ('
            ' session_id, character_id, started_at, addon_version, schema_version,'
            ' client_version, client_build, interface_version, missing_apis_json'
            ') VALUES (?,?,?,?,?,?,?,?,?)'
            ' ON CONFLICT(session_id) DO UPDATE SET'
            '   missing_apis_json = excluded.missing_apis_json',
            (session.get('id') or session_id, character_id,
             _as_int(session.get('startedAt')), session.get('addonVersion'),
             _as_int(session.get('schemaVersion')), session.get('clientVersion'),
             str(session.get('clientBuild')) if session.get('clientBuild') is not None else None,
             _as_int(session.get('interfaceVersion')),
             json.dumps(missing) if missing else None))


# -- phase two: materialize ---------------------------------------------

def materialize(conn):
    """Rebuild every derived table from the events table."""
    db_module.clear_derived(conn)

    rows = conn.execute(
        'SELECT event_id, character_id, event_type, timestamp, zone,'
        '       raw_payload_json FROM events ORDER BY timestamp, event_id'
    ).fetchall()

    quests = {}

    for row in rows:
        payload = json.loads(row['raw_payload_json'])
        event_type = row['event_type']
        source = payload.get('source') or {}
        npc_key = npc_key_for(source)

        if npc_key:
            _record_npc(conn, npc_key, source.get('name'), row['timestamp'], row['zone'])

        if event_type in QUEST_EVENTS:
            _accumulate_quest(quests, row, payload, event_type, npc_key)
        elif event_type == 'QUEST_LOG_SNAPSHOT':
            _accumulate_snapshot(quests, row, payload)

        if event_type == 'QUEST_GREETING':
            greeting = payload.get('greeting') or {}
            _record_dialogue(conn, row, npc_key, None,
                             'quest_greeting', greeting.get('text'))
        elif event_type == 'ZONE_DISCOVERED':
            _record_zone(conn, row, payload.get('discovery') or {})
        elif event_type == 'QUEST_PROGRESS':
            quest = payload.get('quest') or {}
            _record_dialogue(conn, row, npc_key, _as_int(quest.get('id')),
                             'quest_progress', quest.get('progressText'))
        elif event_type == 'QUEST_COMPLETE':
            quest = payload.get('quest') or {}
            _record_dialogue(conn, row, npc_key, _as_int(quest.get('id')),
                             'quest_completion', quest.get('completionText'))
        elif event_type == 'GOSSIP_SHOW':
            gossip = payload.get('gossip') or {}
            _record_dialogue(conn, row, npc_key, None, 'gossip', gossip.get('text'))
        elif event_type in DIALOGUE_EVENTS:
            spoken = payload.get('dialogue') or {}
            _record_dialogue(conn, row, npc_key, None,
                             event_type.replace('CHAT_MSG_MONSTER_', '').lower(),
                             spoken.get('text'))
        elif event_type == 'ITEM_TEXT_READY':
            _record_item_text(conn, row, payload.get('item') or {})

    for (character_id, quest_id), fields in quests.items():
        _write_quest(conn, character_id, quest_id, fields)


def _record_npc(conn, npc_key, display_name, timestamp, zone):
    conn.execute(
        'INSERT INTO npcs (npc_key, display_name, first_seen_at, first_zone)'
        ' VALUES (?,?,?,?)'
        ' ON CONFLICT(npc_key) DO UPDATE SET'
        '   display_name = COALESCE(excluded.display_name, npcs.display_name),'
        # Keep the earliest sighting, regardless of the order rows arrive.
        '   first_seen_at = MIN(COALESCE(npcs.first_seen_at, excluded.first_seen_at),'
        '                       COALESCE(excluded.first_seen_at, npcs.first_seen_at))',
        (npc_key, display_name, timestamp, zone))


def _record_dialogue(conn, row, npc_key, quest_id, dialogue_type, text):
    if not text:
        return
    # The event id is unique and each event yields at most one utterance,
    # so reusing it keeps rebuilds stable instead of minting new ids.
    conn.execute(
        'INSERT OR REPLACE INTO dialogue ('
        ' dialogue_id, event_id, character_id, npc_key, quest_id,'
        ' dialogue_type, text, timestamp) VALUES (?,?,?,?,?,?,?,?)',
        (row['event_id'], row['event_id'], row['character_id'], npc_key,
         quest_id, dialogue_type, text, row['timestamp']))


def _record_item_text(conn, row, item):
    text = item.get('text')
    if not text:
        return
    conn.execute(
        'INSERT OR REPLACE INTO item_text ('
        ' item_text_id, event_id, character_id, item_name, page, text, creator, timestamp'
        ') VALUES (?,?,?,?,?,?,?,?)',
        (row['event_id'], row['event_id'], row['character_id'], item.get('name'),
         _as_int(item.get('page')), text, item.get('creator'), row['timestamp']))


def _accumulate_quest(quests, row, payload, event_type, npc_key):
    quest = payload.get('quest') or {}
    quest_id = _as_int(quest.get('id'))
    if quest_id is None:
        return

    key = (row['character_id'], quest_id)
    fields = quests.setdefault(key, {})
    timestamp = row['timestamp']

    def keep_earliest(name, value):
        if value is None:
            return
        if fields.get(name) is None or value < fields[name]:
            fields[name] = value

    def keep_if_present(name, value):
        if value not in (None, ''):
            fields[name] = value

    keep_earliest('first_seen_at', timestamp)
    keep_if_present('title', quest.get('title'))

    if event_type == 'QUEST_DETAIL':
        keep_if_present('description', quest.get('description'))
        keep_if_present('objectives_text', quest.get('objectivesText'))
        keep_if_present('giver_npc_key', npc_key)
    elif event_type == 'QUEST_ACCEPTED':
        keep_earliest('accepted_at', timestamp)
        keep_if_present('giver_npc_key', npc_key)
    elif event_type == 'QUEST_COMPLETE':
        keep_earliest('completed_at', timestamp)
        keep_if_present('completion_text', quest.get('completionText'))
    elif event_type == 'QUEST_TURNED_IN':
        keep_earliest('turned_in_at', timestamp)
        keep_if_present('xp_reward', _as_int(quest.get('xpReward')))
        keep_if_present('money_reward', _as_int(quest.get('moneyReward')))
    elif event_type == 'QUEST_REMOVED':
        # Recorded as a plain removal here. Whether it was an abandonment
        # cannot be decided from this event alone, because the client fires
        # the same one on turn-in, so the judgment waits until every event
        # for this quest has been seen.
        keep_earliest('removed_at', timestamp)


def _accumulate_snapshot(quests, row, payload):
    """Seed quests that were already in the log when the addon first looked.

    These carry a title and nothing else: no description, no giver, no
    accepted time, because none of it was ever observed. They are flagged so
    a recap can say the character is carrying the quest without inventing
    the story of how they got it.
    """
    entries = (payload.get('questLog') or {}).get('entries') or []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        quest_id = _as_int(entry.get('questId'))
        if quest_id is None:
            continue

        fields = quests.setdefault((row['character_id'], quest_id), {})
        if entry.get('title') and not fields.get('title'):
            fields['title'] = entry['title']
        if fields.get('first_seen_at') is None:
            fields['first_seen_at'] = row['timestamp']
        # Only a quest never seen being accepted is really "known only from
        # a snapshot". If an accept was observed anywhere, that wins.
        fields.setdefault('known_from_snapshot', 1)


def _record_zone(conn, row, discovery):
    zone = discovery.get('zone')
    if not zone:
        return
    conn.execute(
        'INSERT OR REPLACE INTO zones ('
        ' zone, character_id, map_id, subzone, discovered_at, event_id'
        ') VALUES (?,?,?,?,?,?)',
        (zone, row['character_id'], _as_int(discovery.get('mapId')),
         discovery.get('subZone'), row['timestamp'], row['event_id']))


def _resolve_abandonment(fields):
    """Decide whether a removal was an abandonment.

    The client fires one event for both abandoning a quest and handing it
    in, so the event alone cannot say which happened. A turn-in for the same
    quest is decisive evidence that the removal was a completion. With no
    turn-in anywhere in the history, the quest left the log unfinished, and
    that is what abandonment means.

    Left NULL when the evidence is ambiguous rather than guessed, because a
    quest wrongly marked abandoned disappears from the player's open threads.
    """
    removed_at = fields.get('removed_at')
    if removed_at is None:
        return None
    if fields.get('turned_in_at') is not None:
        return None
    return removed_at


def _write_quest(conn, character_id, quest_id, fields):
    # A quest observed being accepted was never "known only from a snapshot",
    # whatever order the events arrived in.
    known_from_snapshot = 1 if (fields.get('known_from_snapshot')
                                and fields.get('accepted_at') is None) else 0

    conn.execute(
        'INSERT OR REPLACE INTO quests ('
        ' character_id, quest_id, title, first_seen_at, accepted_at, completed_at,'
        ' turned_in_at, giver_npc_key, description, objectives_text, completion_text,'
        ' xp_reward, money_reward, abandoned_at, known_from_snapshot'
        ') VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (character_id, quest_id, fields.get('title'), fields.get('first_seen_at'),
         fields.get('accepted_at'), fields.get('completed_at'), fields.get('turned_in_at'),
         fields.get('giver_npc_key'), fields.get('description'),
         fields.get('objectives_text'), fields.get('completion_text'),
         fields.get('xp_reward'), fields.get('money_reward'),
         _resolve_abandonment(fields), known_from_snapshot))


# -- entry point ---------------------------------------------------------

def import_file(conn, path, rematerialize=True):
    """Import one SavedVariables file. Safe to repeat."""
    from pathlib import Path

    source = Path(path)
    data = luaparse.load_file(source)

    payload = data.get(SAVED_VARIABLE_NAME)
    if payload is None:
        raise ValueError('%s does not define %s' % (source, SAVED_VARIABLE_NAME))

    result = ingest_events(conn, payload, source=source)

    if rematerialize:
        materialize(conn)

    stat = source.stat()
    conn.execute(
        'INSERT INTO import_state ('
        ' source_path, last_imported_at, last_file_mtime, last_file_size,'
        ' events_seen, events_inserted, events_skipped) VALUES (?,?,?,?,?,?,?)'
        ' ON CONFLICT(source_path) DO UPDATE SET'
        '   last_imported_at = excluded.last_imported_at,'
        '   last_file_mtime = excluded.last_file_mtime,'
        '   last_file_size = excluded.last_file_size,'
        '   events_seen = excluded.events_seen,'
        '   events_inserted = excluded.events_inserted,'
        '   events_skipped = excluded.events_skipped',
        (str(source.resolve()), int(time.time()), stat.st_mtime, stat.st_size,
         result.events_seen, result.events_inserted, result.events_skipped))

    conn.commit()
    return result
