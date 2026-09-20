from collections.abc import Iterator
import hashlib
from pathlib import Path

from google import genai
from google.genai import types


MODEL = "gemini-3.1-pro-preview"
TEMPERATURE = 1
REQUEST_TIMEOUT_MS = 180_000
API_KEY_PATH = Path(__file__).resolve().parent.parent / "agentplatform_api_key"

PROMPT = r"""Transcribe the attached PDF of handwritten notes into a single
faithful Markdown document. The notes were written on a reMarkable tablet.

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
    f"{MODEL}\0{TEMPERATURE}\0{PROMPT}".encode("utf-8")
).hexdigest()


def load_api_key() -> str:
    api_key = API_KEY_PATH.read_text(encoding="utf-8").strip()
    if not api_key:
        raise RuntimeError(f"API key file is empty: {API_KEY_PATH}")
    return api_key


def stream_pdf_markdown(pdf_bytes: bytes, api_key: str | None = None) -> Iterator[str]:
    with genai.Client(
        vertexai=True,
        api_key=api_key or load_api_key(),
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    ) as client:
        response_stream = client.models.generate_content_stream(
            model=MODEL,
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                PROMPT,
            ],
            config=types.GenerateContentConfig(
                temperature=TEMPERATURE,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )

        yielded_text = False
        for chunk in response_stream:
            if chunk.text:
                yielded_text = True
                yield chunk.text

        if not yielded_text:
            raise RuntimeError("Gemini returned no Markdown text")
