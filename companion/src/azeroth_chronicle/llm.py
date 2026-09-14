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
import re
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
planning, or where a story is heading. The player has not learned that yet. Your \
last sentence must describe something the character actually did, said, or was \
given in this session - not what they might face, wonder, or discover afterward.

4. Write as a chronicle addressed to the player, in second person, past tense. \
Name the characters and places they actually met. Prefer their own quest text's \
framing over your own.

5. Be concise. A few short paragraphs. Do not list every quest mechanically; \
tell the through-line of the session and what it meant for the character.

6. Plain prose only: no markdown of any kind. That means no lines starting with \
#, no ##, no bullet points or numbered lists, no bold or italic asterisks. Write \
paragraphs exactly as they would appear in a printed book, with nothing but \
letters and ordinary punctuation at the start of a line."""

# A chapter and a session recap share every honesty rule - only captured
# text, say when it's thin, never speculate ahead - but not the framing.
# A recap is "what just happened"; a chapter is "this place, across
# whatever of the character's whole history touches it," which is why the
# instruction below asks for a chapter of an ongoing chronicle rather than
# a summary of a session, even though the underlying rules are identical.
CHAPTER_SYSTEM_PROMPT = """You write one chapter of an ongoing chronicle for a \
World of Warcraft player, covering everything their character has experienced \
in a single place, however much real time that took.

Rules, in order of importance:

1. Use only the supplied player-history context. Every name, place, motive and \
event must appear in it. You know a great deal about Warcraft; none of that may \
enter this chapter. The player is discovering this story for the first time and \
an invented or imported detail spoils it.

2. If the context is thin, say so plainly rather than padding. A place the \
character passed through briefly deserves a short chapter, not a padded one.

3. Never speculate about what happens next, what a character is secretly \
planning, or where the story goes from here - this chapter's place here may not \
even be finished, more may happen in this place later, but that is not this \
chapter's business. Your last sentence must describe something the character \
actually did, said, or was given while here - not what they might face, wonder, \
or discover afterward.

4. Write as a chapter of the character's own story, in second person, past \
tense. Name the people and events they actually encountered. Prefer their own \
quest text's framing over your own. Where the record shows the character left \
and returned, or a quest sat unresolved for a time, let the chapter reflect that \
shape rather than flattening it into one continuous visit.

5. Tell the throughline of this place in the character's journey - what \
brought them here, what they did, who they met, what it left them having done -
rather than listing every quest mechanically. If several quests here were \
clearly one continuous errand, a natural in-text transition ("From there, ...") \
tells that better than a new section heading would.

6. Plain prose only: no markdown of any kind. That means no lines starting with \
#, no ##, no bullet points or numbered lists, no bold or italic asterisks. This \
one chapter is a handful of paragraphs, written exactly as they would appear in \
a printed book, with nothing but letters and ordinary punctuation at the start \
of a line."""


_MARKDOWN_HEADER = re.compile(r'^#{1,6}\s*', re.MULTILINE)
_MARKDOWN_BULLET = re.compile(r'^[-*+]\s+', re.MULTILINE)
_MARKDOWN_BOLD = re.compile(r'\*\*(.+?)\*\*')
_MARKDOWN_ITALIC = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)')


def _strip_markdown(text):
    """Remove Markdown a model wrote despite being told not to.

    The instruction is in the system prompt, but a small local model does
    not always follow it - the first real chapter generated during
    development came back with "### A Chapter of the Forest" style
    headers, which would render as literal hash characters in the addon's
    plain-text display rather than as anything resembling a heading. This
    is not optional cleanup; without it, a model that ignores the
    instruction produces visibly broken output in-game.

    Deliberately narrow: headers and bullets are stripped down to their
    text since those are unambiguous line-start markers, and bold/italic
    markers are unwrapped rather than deleted so the words survive. This
    cannot catch every way a model might violate "plain prose," only the
    concrete pattern actually observed.
    """
    text = _MARKDOWN_HEADER.sub('', text)
    text = _MARKDOWN_BULLET.sub('', text)
    text = _MARKDOWN_BOLD.sub(r'\1', text)
    text = _MARKDOWN_ITALIC.sub(r'\1', text)
    return text


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


def summarize(context_text, system_prompt=None, instruction=None, model=None,
             base_url=None, max_output_tokens=MAX_OUTPUT_TOKENS, num_ctx=None):
    """Send one context to the local model and return the recap text.

    `num_ctx` is left unset (Ollama's own runtime default) unless a caller
    asks for more - a chapter covering a whole zone's history can be far
    larger than one session, and left to a small ambient default the model
    would simply see less of the context than the prompt actually contains,
    silently. Not raised to the model's full trained limit here even when
    requested: this hardware measurably struggles with a 7B model already,
    and a bigger KV cache means less of it fits in the 6GB of VRAM observed
    during development, pushing more of the run onto the slower CPU path.
    """
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

    options = {'num_predict': max_output_tokens}
    if num_ctx is not None:
        options['num_ctx'] = num_ctx

    try:
        response = _post(base_url, '/api/chat', {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt or SYSTEM_PROMPT},
                {'role': 'user', 'content': (instruction or 'Recap this stretch of play.')
                 + '\n\n' + context_text},
            ],
            'stream': False,
            'options': options,
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

    text = _strip_markdown(text).strip()

    return {
        'text': text,
        'model': response.get('model', model),
        # Ollama's own token-ish counters; close enough for a rough cost/
        # length readout, not billed against anything since this is local.
        'input_tokens': response.get('prompt_eval_count'),
        'output_tokens': response.get('eval_count'),
    }
