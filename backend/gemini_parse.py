"""
Layer 1c (opt-in) — poore PDF ko Google Gemini vision se seedha padh kar
structure banata hai, geometry rules ko bypass karke.

NON-NEGOTIABLE: ye path kabhi apne aap nahi chalta — sirf jab user khud "AI
Parse" click kare. Local rules-pipeline (extract.py + classify.py + Tesseract
+ memory.py) hamesha default hai aur har upload isse guzarta hai; ye module
sirf ek on-demand alternative hai jab local result kamzor lage.

Free tier + koi extra SDK dependency nahi — seedha REST API (`urllib`) se
call karte hain. Free tier ki per-minute rate-limit se bachne ke liye har
request ke beech kam se kam MIN_REQUEST_GAP_SECONDS ka gap rakha jaata hai,
aur 429 (rate limited) par exponential backoff se retry hota hai — bina iske
multi-page PDF ka bulk parse lagbhag pakka fail hota.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

import pdfplumber

from . import memory as memory_module
from .schema import Column, Component, Option, Template, slugify

GEMINI_MODEL = os.environ.get("FORMFORGE_GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
MAX_PAGES = 20

# Free tier per-minute cap ~10-15 requests — 5s gap = max 12/min, safe margin.
MIN_REQUEST_GAP_SECONDS = 5.0
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 4.0

ALLOWED_TYPES = {
    "heading", "subheading", "paragraph", "textbox", "textarea", "number", "date",
    "time", "dropdown", "radiogroup", "checkboxgroup", "checkbox", "table",
    "signature", "fileupload", "divider",
}

# < 0.8 jaan-boojh kar — vision pass poora page "dekh" kar samajhta hai
# (rules-only se behtar), par 1.0 nahi — reviewer check kare.
AI_CONFIDENCE = 0.75

SYSTEM_PROMPT = """You convert one page of a printed/scanned form into a structured digital \
form schema by actually reading and understanding the page image — not just detecting lines \
and boxes. Read it the way a careful human form-builder would: understand what each visual \
group of text and marks *means*, not just where the pixels are.

Allowed component types: heading, subheading, paragraph, textbox, textarea, number, date, \
time, dropdown, radiogroup, checkboxgroup, checkbox, table, signature, fileupload, divider.

Key judgment calls to get right:

1. MULTI-COLUMN FIELD ROWS: forms often put two or three unrelated fields side by side on one \
visual row (e.g. "Name: ______        Height: ______"). Each is a SEPARATE field with its own \
label — never merge them into one field with a combined label.

2. CHECKBOX GROUP LABEL vs OPTIONS: a line like "Food Habits:  [] Veg  [] Non-veg  [] Ovo-veg" \
has ONE label ("Food Habits") and THREE options (Veg, Non-veg, Ovo-veg). The label is never one \
of the options, even when there's no visible checkbox glyph and options are just spaced-out \
words the reader is meant to circle or tick.

3. WRAPPED OPTION LISTS: if a checkbox group's options continue onto a second visual row with \
no new label, these are the SAME group — combine all options into one component.

4. TABLES: reconstruct a table whenever you see a genuine row/column grid of data — even if the \
page has no visible ruled lines. Give it columns (from the header row) and a rowCount.

5. NESTED / NUMBERED LISTS: numbered items that each contain their own checkbox options or \
sub-fields should become their own labeled component — don't flatten everything into one blob.

6. BLANK FIELDS: a label followed by a ruled line, underscores, or empty space clearly meant for \
writing is an input field — pick the narrowest sensible type (date for dates, number for numeric \
measures, textarea for "describe/list" prompts, signature for a signature line, else textbox).

7. STATIC TEXT: page titles, section captions, letterhead, addresses, footers are \
heading/subheading/paragraph — never turn static text into an input field.

Strip list numbering ("1.", "a)") from labels. Keep labels short and human-readable. Preserve \
top-to-bottom, left-to-right reading order in the output array.

Return ONLY a JSON array, one object per component, in reading order:
{"type": "<type>", "label": "<string>", "options": ["A","B"], "columns": ["Col1","Col2"], "rowCount": <int>}
Omit keys that don't apply. No prose, no markdown fences — JSON array only.
"""


class GeminiUnavailable(RuntimeError):
    """GEMINI_API_KEY set nahi hai, admin ne band kar rakha hai, ya API call fail hui."""


def is_disabled_by_admin() -> bool:
    return os.environ.get("FORMFORGE_AI_DISABLED", "").strip().lower() in ("1", "true", "yes")


def availability() -> Dict[str, Any]:
    """Frontend ke liye: AI Parse button enable/disable karna ho to yahan se pata chalta hai."""
    if is_disabled_by_admin():
        return {"available": False, "reason": "AI Parse has been disabled by the admin"}
    if not os.environ.get("GEMINI_API_KEY"):
        return {"available": False, "reason": "GEMINI_API_KEY is not set"}
    return {"available": True, "reason": None}


def _render_page_png(page: Any, resolution: int = 200) -> bytes:
    img = page.to_image(resolution=resolution)
    buf = io.BytesIO()
    img.original.save(buf, format="PNG")
    return buf.getvalue()


def _few_shot_block() -> str:
    examples = memory_module.top_examples(8)
    if not examples:
        return ""
    lines = []
    for e in examples:
        extra = f", options={e['options']}" if e.get("options") else ""
        lines.append(f'- "{e["line"]}" -> type={e["type"]}{extra}')
    return (
        "\n\nThese type corrections were learned from real forms this system has seen before "
        "(use as guidance for similar lines, not as literal string matches):\n" + "\n".join(lines)
    )


_last_request_time = 0.0


def _call_gemini(png: bytes, page_no: int) -> str:
    global _last_request_time
    info = availability()
    if not info["available"]:
        raise GeminiUnavailable(info["reason"])

    elapsed = time.time() - _last_request_time
    if elapsed < MIN_REQUEST_GAP_SECONDS:
        time.sleep(MIN_REQUEST_GAP_SECONDS - elapsed)

    b64 = base64.b64encode(png).decode("ascii")
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + _few_shot_block()}]},
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/png", "data": b64}},
                {"text": f"Page {page_no}. Return the JSON array now."},
            ],
        }],
        "generationConfig": {"temperature": 0.1},
    }
    body = json.dumps(payload).encode("utf-8")
    url = f"{GEMINI_URL}?key={os.environ['GEMINI_API_KEY']}"
    req = urllib.request.Request(url, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})

    backoff = BACKOFF_BASE_SECONDS
    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            _last_request_time = time.time()
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            candidates = data.get("candidates") or []
            if not candidates:
                raise GeminiUnavailable(f"Gemini returned no response (page {page_no}).")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            if not text.strip():
                raise GeminiUnavailable(f"Gemini's response was empty (page {page_no}).")
            return text
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            last_error = f"HTTP {exc.code}: {detail[:300]}"
            if exc.code == 429 and attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise GeminiUnavailable(f"Gemini API call failed (page {page_no}): {last_error}") from exc
        except urllib.error.URLError as exc:
            raise GeminiUnavailable(f"Could not reach Gemini (page {page_no}): {exc}") from exc

    raise GeminiUnavailable(
        f"Gemini rate limit — still failing after {MAX_RETRIES} retries (page {page_no}): {last_error}"
    )


def _parse_page(png: bytes, page_no: int) -> List[Component]:
    text = _call_gemini(png, page_no).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        raise GeminiUnavailable(f"Gemini did not return valid JSON (page {page_no}).")
    try:
        items = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise GeminiUnavailable(f"Could not parse Gemini's JSON (page {page_no}): {exc}") from exc

    components: List[Component] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ctype = item.get("type", "textbox")
        label = str(item.get("label", "")).strip()
        options = [Option.make(str(o)) for o in item.get("options", []) if str(o).strip()]
        columns = [
            Column(key=slugify(str(col), f"col_{i+1}"), label=str(col))
            for i, col in enumerate(item.get("columns", []))
        ]
        components.append(Component(
            type=ctype if ctype in ALLOWED_TYPES else "textbox",
            label=label[:200],
            options=options,
            columns=columns,
            rowCount=int(item.get("rowCount", 3) or 3),
            confidence=AI_CONFIDENCE,
            detectedBy="ai",
            source={"page": page_no, "ai": True},
        ))
    return components


def gemini_parse_document(path: str, title: str = "") -> Template:
    info = availability()
    if not info["available"]:
        raise GeminiUnavailable(
            f"{info['reason']}. Add GEMINI_API_KEY=... to .env and restart the server "
            "(get a free key at https://aistudio.google.com/apikey)."
        )

    components: List[Component] = []
    page_count = 0
    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
            png = _render_page_png(page)
            components.extend(_parse_page(png, idx))
            page_count = idx

    seen: Dict[str, int] = {}
    for c in components:
        base = c.key or slugify(c.label, c.type)
        n = seen.get(base, 0)
        seen[base] = n + 1
        c.key = base if n == 0 else f"{base}_{n+1}"

    if not title:
        title = next((c.label for c in components if c.type == "heading"), "Imported Form")

    tpl = Template(title=title, components=components,
                    meta={"pages": page_count, "generator": "formforge-gemini"})
    return tpl.renumber()
