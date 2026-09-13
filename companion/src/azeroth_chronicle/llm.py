"""The only part of this project that touches the network.

Import, query and the journal pane all work with nothing installed and no
account. This module is the single optional dependency, imported lazily so
that a missing package or a missing key degrades exactly one feature instead
of breaking the tool.

The system prompt below is load-bearing, not decoration. The context handed
to the model contains only what the player encountered, and the instruction
tells the model to stay inside it. Both halves are needed: retrieval decides
what can be known, the instruction stops the model filling gaps from its own
knowledge of Warcraft. For a player discovering a new game's story slowly,
an invented detail is worse than no summary at all.
"""

MODEL = 'claude-opus-5'

# Recaps are short by design, so this cap is deliberate rather than careless.
MAX_TOKENS = 2000

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


class LlmRefused(Exception):
    """The model declined to answer, including after any fallback."""


def _client():
    try:
        import anthropic
    except ImportError:
        raise LlmUnavailable(
            'The recap feature needs the anthropic package, which is the only\n'
            'dependency in this project.\n\n'
            '    pip install anthropic\n\n'
            'Everything else (import, status, quests, show) works without it.')

    try:
        return anthropic.Anthropic()
    except Exception as exc:
        raise LlmUnavailable(
            'Could not create an API client: %s\n\n'
            'Set ANTHROPIC_API_KEY, or sign in with `ant auth login`.\n'
            'The key is never written to the addon or to your journal.' % exc)


def summarize(context_text, system_prompt=None, model=MODEL, max_tokens=MAX_TOKENS):
    """Send one context to the model and return the recap text.

    Server-side fallbacks are enabled: if the model declines the request on
    policy grounds, the API retries it on a fallback model within the same
    call rather than simply returning nothing.
    """
    client = _client()

    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt or SYSTEM_PROMPT,
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            messages=[{
                'role': 'user',
                'content': 'Recap this stretch of play.\n\n' + context_text,
            }],
        )
    except Exception as exc:
        # Anything from the network down to a bad key lands here. The point
        # is that a recap failure never takes the rest of the tool with it.
        raise LlmUnavailable('The request failed: %s' % exc)

    if response.stop_reason == 'refusal':
        detail = ''
        if getattr(response, 'stop_details', None):
            detail = ' (%s)' % response.stop_details.category
        raise LlmRefused('The model declined to write this recap%s.' % detail)

    parts = [block.text for block in response.content if block.type == 'text']
    text = '\n'.join(parts).strip()

    if not text:
        raise LlmUnavailable('The model returned no text.')

    return {
        'text': text,
        'model': response.model,
        'input_tokens': getattr(response.usage, 'input_tokens', None),
        'output_tokens': getattr(response.usage, 'output_tokens', None),
    }
