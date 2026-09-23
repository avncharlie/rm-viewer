from collections.abc import Iterator
import hashlib
import logging
from pathlib import Path
import time

from google import genai
from google.genai import types


log = logging.getLogger(__name__)

MODEL = "gemini-3.1-pro-preview"
TEMPERATURE = 1
THINKING_LEVEL = types.ThinkingLevel.LOW
REQUEST_TIMEOUT_MS = 180_000
API_KEY_PATH = Path(__file__).resolve().parent.parent / "agentplatform_api_key"

PROMPT = r""" Convert the attached PDF into a single faithful Markdown
document. It may contain handwritten notes, typed or printed pages, imported
documents, images, and handwritten annotations over printed content.

# Method

Read the whole document before writing anything. Work out how the author
signals structure (underlined or larger titles, indentation, bullet marks,
numbering, boxes, spacing) and apply one consistent mapping to the entire
output. Use context from the whole document to resolve ambiguous handwriting.
A word that is unclear on page 2 is often written clearly on page 5.

# Output syntax

The output is rendered by Markdown-it with CommonMark 0.31.2 as the baseline,
plus these extensions:
- GFM pipe tables, with column alignment.
- KaTeX math: `$...$` inline, and display math with `$$` on its own line
  before and after. Use valid LaTeX inside the delimiters.
- Mermaid diagrams in fenced code blocks labelled `mermaid`.
- Language-labelled fenced code blocks.
- `~~strikethrough~~` and task list items (`- [ ]`, `- [x]`).

Line breaks
- Markdown does not preserve source newlines. Consecutive lines flow together
  into one paragraph when rendered, so a line break in your output is not a
  line break on screen.
- Separate every block from the next with one blank line: paragraphs, headings,
  lists, block quotes, tables, code fences and Mermaid blocks. A list or table
  that directly follows a paragraph line will not render correctly.
- When the author's own line break is meaningful but the lines are one block
  (verse, an address, a short stacked list of terms, a signature line, a
  definition and its gloss), end the line with a single backslash `\` to force
  a hard break. Do not use trailing double spaces for this.
- Use a hard break only where the break carries meaning. Text that merely wrapped
  at the edge of the page is still joined into one paragraph, per the rules below.

Do not use raw HTML, a code fence around the whole answer, or any syntax not
listed above.

# Transcription rules

Text
- Transcribe the author's words exactly. Do not summarize, rephrase, expand
  abbreviations, correct spelling or grammar, or add commentary.
- Handwritten line breaks are usually just the edge of the page. Join wrapped
  lines into one paragraph or list item, and rejoin words hyphenated across
  lines. Start a new paragraph only where the author clearly did (a blank gap,
  a new indent level, a new bullet).
- Ignore the page template (ruled lines, grids, dots, margins), page numbers
  and UI artifacts. They are not content.
- Pages are an artifact of the device. If a sentence, list or code block
  continues across a page boundary, continue it seamlessly. Never emit page
  markers, rules or headings for page breaks.

Structure
- Map the author's title levels to `#`, `##`, `###` consistently. Do not
  invent headings the author did not write.
- Preserve list nesting from indentation. Dashes, dots, arrows-as-bullets and
  similar marks become `-` items. Numbered items stay numbered. Hand-drawn
  checkboxes become task list items, ticked or not as drawn.
- A hooked right arrow used as indentation should be expressed as `$\hookrightarrow$`
- Underlined, boxed, circled, starred or highlighted words are emphasis. Use
  `**strong**` for those. Use `*emphasis*` only for clearly lighter emphasis.
  Apply it to the words marked, not the whole line.
- Hand-drawn grids and aligned columns become pipe tables.

Corrections and annotations
- Place margin notes and arrowed asides immediately after the text they refer
  to, as a block quote. If the target is unclear, place the note at the
  nearest position in reading order.
- An arrow in running text that means "leads to" or "implies" becomes `→` (or
  `←`, `↔`, `⇒` as drawn).

Code and technical content
- Use inline code for identifiers, function and API names, commands, file
  paths, registers, hex values, addresses and flags. Use fenced blocks for
  multi-line code, shell sessions, assembly, struct layouts and memory dumps,
  labelled with the language when you can tell.
- Inside code, preserve characters exactly, including case, underscores,
  punctuation and indentation. Be careful with lookalikes (`0`/`O`, `1`/`l`/`I`,
  `_`/`-`, `;`/`:`). Resolve these using language syntax and nearby usage,
  not guesswork.
- Transcribe handwritten maths as KaTeX, not Unicode approximations.

Diagrams and sketches
- If a drawing is a flow, hierarchy, state machine, sequence, timeline or
  relationship graph that Mermaid can represent accurately, write it as
  Mermaid using the author's own labels.
- In Mermaid labels, wrap every LaTeX expression in `$$...$$`, for example
  `A["SSA uses $$\phi$$ nodes"]`. The surrounding Markdown renderer does not
  process math inside Mermaid fences, so never write a bare command such as
  `\phi` in a Mermaid label.
- Memory layouts, stack frames, packet or struct layouts are usually clearer
  as a table or a fenced text block than as Mermaid.
- Otherwise, put a brief italic description in the drawing's position, e.g.
  `*Sketch: heap chunks with an arrow from the freed chunk to the tcache
  bin.*` Include any labels you can read. Do not guess details.

Uncertainty
- If you can read a word with reasonable confidence from its shape and
  context, write it plainly.
- If you have a best guess but it is truly uncertain, write it as
  `[guess?]`.
- If it cannot be read at all, write `[illegible]`. Never silently fabricate
  text or skip it. Never use either marker inside a code block without also
  keeping the surrounding code intact.

# Output
Include every page. Return only the Markdown document, with no preamble, no
closing remarks and no notes about the transcription.
"""

GENERATOR_SIGNATURE = hashlib.sha256(
    f"{MODEL}\0{TEMPERATURE}\0{THINKING_LEVEL.value}\0{PROMPT}".encode("utf-8")
).hexdigest()


def load_api_key() -> str:
    api_key = API_KEY_PATH.read_text(encoding="utf-8").strip()
    if not api_key:
        raise RuntimeError(f"API key file is empty: {API_KEY_PATH}")
    return api_key


def stream_pdf_markdown(
    pdf_bytes: bytes,
    api_key: str | None = None,
    request_id: str | None = None,
) -> Iterator[str]:
    started = time.monotonic()
    request_label = request_id or 'untracked'
    raw_chunks = 0
    text_chunks = 0
    text_chars = 0
    completed = False

    def debug_log(event: str, **details):
        if not log.isEnabledFor(logging.DEBUG):
            return
        fields = ' '.join(
            f'{name}={value!r}' for name, value in sorted(details.items())
        )
        suffix = f' {fields}' if fields else ''
        log.debug(
            'gemini request=%s elapsed=%.3fs event=%s%s',
            request_label,
            time.monotonic() - started,
            event,
            suffix,
        )

    debug_log(
        'client_opening',
        model=MODEL,
        temperature=TEMPERATURE,
        thinking_level=THINKING_LEVEL.value,
        timeout_ms=REQUEST_TIMEOUT_MS,
        pdf_bytes=len(pdf_bytes),
        api_key_source='argument' if api_key else str(API_KEY_PATH),
    )
    try:
        with genai.Client(
            vertexai=True,
            api_key=api_key or load_api_key(),
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        ) as client:
            debug_log('request_starting')
            response_stream = client.models.generate_content_stream(
                model=MODEL,
                contents=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=TEMPERATURE,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=THINKING_LEVEL,
                    ),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
            debug_log('response_stream_opened')

            for chunk in response_stream:
                raw_chunks += 1
                text = chunk.text or ''
                candidates = getattr(chunk, 'candidates', None) or []
                finish_reasons = [
                    str(getattr(candidate, 'finish_reason', None))
                    for candidate in candidates
                    if getattr(candidate, 'finish_reason', None) is not None
                ]
                debug_log(
                    'raw_chunk_received',
                    raw_chunk=raw_chunks,
                    text_chars=len(text),
                    candidate_count=len(candidates),
                    finish_reasons=finish_reasons,
                    response_id=getattr(chunk, 'response_id', None),
                    model_version=getattr(chunk, 'model_version', None),
                    usage_metadata=repr(getattr(chunk, 'usage_metadata', None)),
                )
                if text:
                    text_chunks += 1
                    text_chars += len(text)
                    yield text

            if not text_chunks:
                debug_log('no_text_returned', raw_chunks=raw_chunks)
                raise RuntimeError("Gemini returned no Markdown text")
            completed = True
            debug_log(
                'completed',
                raw_chunks=raw_chunks,
                text_chunks=text_chunks,
                text_chars=text_chars,
            )
    except GeneratorExit:
        debug_log(
            'closed_by_caller',
            raw_chunks=raw_chunks,
            text_chunks=text_chunks,
            text_chars=text_chars,
        )
        raise
    except Exception:
        log.exception(
            'Gemini request %s failed after %.3fs (raw_chunks=%d, '
            'text_chunks=%d, text_chars=%d)',
            request_label,
            time.monotonic() - started,
            raw_chunks,
            text_chunks,
            text_chars,
        )
        raise
    finally:
        debug_log(
            'client_closed',
            completed=completed,
            raw_chunks=raw_chunks,
            text_chunks=text_chunks,
            text_chars=text_chars,
        )
