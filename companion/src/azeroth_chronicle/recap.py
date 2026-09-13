"""Generate and persist the recap for one play session.

Shared by the `recap` CLI command and the background watcher, so there is
exactly one implementation deciding what counts as "already recapped" (an
unchanged context hash) rather than two that could drift apart.
"""
import json
import time

from . import context as context_module
from . import llm as llm_module


class RecapOutcome:
    def __init__(self, text, model, cached, event_ids, stats, digest,
                input_tokens=None, output_tokens=None):
        self.text = text
        self.model = model
        self.cached = cached  # True if this was already stored, unchanged
        self.event_ids = event_ids
        self.stats = stats
        self.digest = digest
        self.input_tokens = input_tokens    # None when cached: nothing was spent
        self.output_tokens = output_tokens


def generate_and_save_recap(conn, session, max_quests=None, force=False,
                            save=True, on_will_generate=None):
    """Build the context for one session and get its recap.

    Returns cached text without touching the model if an identical context
    was already summarized, unless `force` is set. Raises
    `llm.LlmUnavailable` if generation is needed and the local model cannot
    be reached; callers decide whether that should stop a batch or just
    skip one session.

    `on_will_generate`, if given, is called right before the model is
    actually invoked - never on a cache hit - so a caller can print or log
    "this may take a minute" exactly when that becomes true, rather than
    every time regardless of whether anything is about to happen.
    """
    text, event_ids, stats = context_module.build_recap_context(
        conn, session, max_quests=max_quests)
    digest = context_module.context_hash(text)

    cached = conn.execute(
        'SELECT text, model FROM summaries WHERE context_hash = ?', (digest,)
    ).fetchone()

    if cached and not force:
        return RecapOutcome(cached['text'], cached['model'], True,
                            event_ids, stats, digest)

    if on_will_generate:
        on_will_generate()

    result = llm_module.summarize(text)

    if save:
        character = conn.execute(
            'SELECT character_id FROM characters LIMIT 1').fetchone()
        conn.execute(
            'INSERT OR REPLACE INTO summaries ('
            ' summary_id, character_id, kind, window_start, window_end, model,'
            ' created_at, context_hash, text, source_event_ids_json'
            ') VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('recap-%s' % digest, character['character_id'] if character else None,
             'session_recap', session.start, session.end, result['model'],
             int(time.time()), digest, result['text'], json.dumps(event_ids)))
        conn.commit()

    return RecapOutcome(result['text'], result['model'], False,
                        event_ids, stats, digest,
                        input_tokens=result.get('input_tokens'),
                        output_tokens=result.get('output_tokens'))
