"""The only part of this project that talks to a model, and it never leaves
the machine.

Recaps run against a local Ollama server (https://ollama.com), reached over
plain HTTP with the standard library. No pip package, no API key, no per-
token cost, no game text ever crossing the network. That makes this module
consistent with the rest of the companion rather than an exception to it:
import, query, and the journal pane were already local-first, and now the
recap is too.

The tradeoff, stated plainly: a small local model writes noticeably weaker
prose than a large hosted one, and generation takes on the order of a
minute rather than a few seconds, since most of this project's development
hardware only partially offloads a 7B model to its GPU. Nothing here hides
that; it is what "free and private" costs on modest hardware.

The system prompt below is load-bearing, not decoration. The context handed
to the model contains only what the player encountered, and the instruction
tells the model to stay inside it. Both halves are needed: retrieval decides
what can be known, the instruction stops the model filling gaps from its own
knowledge of Warcraft. For a player discovering a new game's story slowly,
an invented detail is worse than no summary at all. A small local model
follows this instruction less reliably than a large one, so read the output
against the source context rather than trusting it outright.
"""
import json
import os
import urllib.error
import urllib.request

# Overridable via environment, matching Ollama's own OLLAMA_HOST convention
# for the host and a project-specific variable for the model, so switching
# models never requires editing code.
DEFAULT_BASE_URL = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
if not DEFAULT_BASE_URL.startswith('http'):
    # OLLAMA_HOST is sometimes just "host:port"; the server needs a scheme.
    DEFAULT_BASE_URL = 'http://' + DEFAULT_BASE_URL

MODEL = os.environ.get('AZEROTH_CHRONICLE_MODEL', 'qwen2.5:7b-instruct')

# Local generation is slow on modest hardware. Cold model load alone ran
# past a minute during development; a few hundred output tokens on top of
# that needs real headroom, not a cloud-API-sized timeout.
REQUEST_TIMEOUT_SECONDS = 600

# Recaps are short by design, so this cap is deliberate rather than careless,
# and it also bounds how long a slow local model spends generating.
MAX_OUTPUT_TOKENS = 800

SYSTEM_PROMPT = """You write a personal chronicle for a World of Warcraft player, \
recapping what their character just did.

Rules, in order of importance:

1. Use only the supplied player-history context. Every name, place, motive and \
event must appear in it. You know a great deal about Warcraft; none of that may \
enter this recap. The player is discovering this story for the first time and an \
invented or imported detail spoils it.

2. If the context is thin, say so plainly rather than padding. "You spoke with \
few people and finished one errand" is a fine recap of a quiet session.

3. Never speculate about what happens next, what a character is secretly \
planning, or where a story is heading. The player has not learned that yet.

4. Write as a chronicle addressed to the player, in second person, past tense. \
Name the characters and places they actually met. Prefer their own quest text's \
framing over your own.

5. Be concise. A few short paragraphs. Do not list every quest mechanically; \
tell the through-line of the session and what it meant for the character.

6. Do not use headers, bullet points, or markdown. This is prose."""


class LlmUnavailable(Exception):
    """Raised when the feature cannot run, with an actionable message."""


def _post(base_url, path, payload):
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        base_url.rstrip('/') + path, data=data,
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode('utf-8'))


def is_available(base_url=None):
    """Whether an Ollama server is actually reachable right now."""
    try:
        request = urllib.request.Request((base_url or DEFAULT_BASE_URL) + '/api/version')
        with urllib.request.urlopen(request, timeout=3):
            return True
    except Exception:
        return False


def summarize(context_text, system_prompt=None, model=None, base_url=None,
             max_output_tokens=MAX_OUTPUT_TOKENS):
    """Send one context to the local model and return the recap text."""
    base_url = base_url or DEFAULT_BASE_URL
    model = model or MODEL

    if not is_available(base_url):
        raise LlmUnavailable(
            'Could not reach a local model server at %s.\n\n'
            'Install Ollama (https://ollama.com) if you have not, then make sure\n'
            'it is running. On Windows it starts automatically after install and\n'
            'runs in the background; if it is not, start the Ollama app or run:\n\n'
            '    ollama serve\n\n'
            'Nothing here ever needs a paid API key or sends your journal over\n'
            'the network.' % base_url)

    try:
        response = _post(base_url, '/api/chat', {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt or SYSTEM_PROMPT},
                {'role': 'user', 'content': 'Recap this stretch of play.\n\n' + context_text},
            ],
            'stream': False,
            'options': {'num_predict': max_output_tokens},
        })
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', 'replace')
        if exc.code == 404:
            raise LlmUnavailable(
                'Model "%s" is not pulled yet. Run:\n\n    ollama pull %s'
                % (model, model))
        raise LlmUnavailable('The local model server returned an error: %s' % body)
    except urllib.error.URLError as exc:
        raise LlmUnavailable(
            'Could not reach the local model server: %s\n\n'
            'It answered a moment ago and may have been stopped mid-request.'
            % exc.reason)
    except TimeoutError:
        raise LlmUnavailable(
            'The local model did not respond within %d seconds. A cold model'
            ' load or a long context can take a while on modest hardware; try'
            ' again, or pass --max-quests to shrink the context.'
            % REQUEST_TIMEOUT_SECONDS)

    text = ((response.get('message') or {}).get('content') or '').strip()
    if not text:
        raise LlmUnavailable('The model returned no text.')

    return {
        'text': text,
        'model': response.get('model', model),
        # Ollama's own token-ish counters; close enough for a rough cost/
        # length readout, not billed against anything since this is local.
        'input_tokens': response.get('prompt_eval_count'),
        'output_tokens': response.get('eval_count'),
    }
