"""
Layer 2 — Layout ko form components mein badalna (ye system ka "dimaag" hai).

Deterministic heuristics chalte hain pehle. Har component ke saath confidence
score attach hota hai; is ke baad learned correction-memory (memory.py) apply
hoti hai, aur low-confidence components builder UI mein highlight hote hain.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .extract import PageLayout, Rule, TextLine, Word, _cluster_by_gap, extract_pdf
from .schema import Column, Component, Option, Template, slugify

# ---------------------------------------------------------------------------
# regex / keyword tables
# ---------------------------------------------------------------------------
BLANK_RE = re.compile(r"_{2,}")
LEAD_NUM_RE = re.compile(r"^\s*(\d{1,2}\s*[.)]|[a-hA-H]\s*[.)]|[ivxIVX]{1,4}\s*[.)])\s*")
TRAIL_MARK_RE = re.compile(r"\s+[a-hA-H]\s*\.?\s*$")

YESNO_PATTERNS = [
    (re.compile(r"(?i)\bnot\s*sure\s+yes\s+no\s*$"), ["Not sure", "Yes", "No"]),
    (re.compile(r"(?i)\byes\s+no\s+not\s*sure\s*$"), ["Yes", "No", "Not sure"]),
    (re.compile(r"(?i)\byes\s*/\s*no\s*$"), ["Yes", "No"]),
    (re.compile(r"(?i)\byes\s+no\s*$"), ["Yes", "No"]),
    (re.compile(r"(?i)\bmale\s+female\s*$"), ["Male", "Female"]),
]

DATE_KW = ("date", "dob", "d.o.b", "birth", "admission on", "discharge on")
TIME_KW = ("time", "timing")
SIGN_KW = ("signature", "sign of", "signed by", "sign:")
NUMBER_KW = (
    "age", "weight", "height", "bmi", "pulse", "temperature", "creatinine",
    "count", "dose", "quantity", "amount", "total", "score", "inr", "hb",
    "ptt", "bt", "ct", "pt", "spo2", "bp",
)
LONGTEXT_KW = (
    "please list", "describe", "remark", "comment", "history", "details",
    "specify", "diagnosis", "complaint", "notes", "instructions", "reason",
    "findings", "advice", "treatment",
)
FILE_KW = ("attach", "upload", "enclose", "document copy")

MAX_LABEL_LEN = 160


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def clean_label(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    t = LEAD_NUM_RE.sub("", t)
    t = TRAIL_MARK_RE.sub("", t)
    t = t.strip(" .:;-–—")
    return t.strip()


def looks_upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) > 0.85


def infer_input_type(label: str, blank_width: float = 0.0, page_width: float = 600.0) -> str:
    low = label.lower()
    if any(k in low for k in SIGN_KW):
        return "signature"
    if any(k in low for k in FILE_KW):
        return "fileupload"
    if any(k in low for k in DATE_KW):
        return "date"
    if any(k in low for k in TIME_KW) and "timing of" not in low:
        return "time"
    if any(re.search(rf"\b{re.escape(k)}\b", low) for k in NUMBER_KW):
        return "number"
    if any(k in low for k in LONGTEXT_KW):
        return "textarea"
    if blank_width > page_width * 0.62:
        return "textarea"
    return "textbox"


def blank_markers(ln: TextLine) -> List[Tuple[float, float]]:
    """Line pe blanks kahan hain — underscores + ruled underlines dono se."""
    marks: List[Tuple[float, float]] = []
    for w in ln.words:
        if BLANK_RE.fullmatch(w.text):
            marks.append((w.x0, w.x1))
        elif BLANK_RE.search(w.text):
            # e.g. "period_____" -> underscore part ka approx x range
            frac = w.text.index("_") / max(len(w.text), 1)
            split_x = w.x0 + (w.x1 - w.x0) * frac
            marks.append((split_x, w.x1))
    for r in ln.rules:
        if not any(not (r.x1 < mx0 or r.x0 > mx1) for mx0, mx1 in marks):
            marks.append((r.x0, r.x1))
    return sorted(marks)


def words_between(ln: TextLine, x_start: float, x_end: float) -> str:
    return " ".join(
        w.text for w in ln.words
        if w.x0 >= x_start - 1 and w.x1 <= x_end + 1 and not BLANK_RE.fullmatch(w.text)
    ).strip()


def strip_blanks(text: str) -> str:
    return BLANK_RE.sub(" ", text).strip()


# Bahut saare paper forms options ke aage checkbox square print nahi karte —
# bas words ko wide-space se alag rakh dete hain (jaise "CT Scan   IVP   Angiogram")
# aur banda haath se circle/tick karta hai. Real checkbox se do gap categories
# alag rehte hain: ek line ke andar normal word-gap (~3-8pt) aur option-se-option
# gap (yahan dekha gaya: 40-70pt) — beech mein safe margin ke saath threshold.
OPTION_GAP_MIN = 18.0
OPTION_MAX_WORDS = 6
OPTION_MAX_CHARS = 40


JUNK_TOKEN_RE = re.compile(r"^[=\-_~.]+$")


def _cluster_texts(clusters: List[List[Word]]) -> Optional[List[str]]:
    """Pehle se bane clusters ko clean label strings mein badalta hai; None
    dega agar koi (asli) cluster bahut lamba hai."""
    labels = []
    for cluster in clusters:
        # OCR kabhi-kabhi ruled blank-line ko khud "=" / "-" jaisa character
        # samajh leta hai — asli option-text ke saath bhi juda ho sakta hai.
        real = [w for w in cluster if not JUNK_TOKEN_RE.fullmatch(w.text)]
        if not real:
            continue
        text = clean_label(" ".join(w.text for w in real))
        if not text:
            continue
        if len(text) > OPTION_MAX_CHARS or len(real) > OPTION_MAX_WORDS:
            return None
        labels.append(text)
    return labels


def _option_clusters(words: List[Word]) -> Optional[List[str]]:
    """Words ko gap se cluster karta hai; None dega agar ye option-row jaisa
    nahi lagta (koi cluster bahut lamba hai, ya <2 clusters hain)."""
    labels = _cluster_texts(_cluster_by_gap(words, OPTION_GAP_MIN))
    if labels is None or len(labels) < 2:
        return None
    return labels


def detect_option_row(ln: TextLine, page_width: float) -> Optional[List[Component]]:
    """Checkbox glyph print nahi hui, par options clearly wide-gap se print
    hain. Confidence jaan-boojh kar kam (real square se detect hone se kam)
    rakhi hai — inferred hai, geometry-certain nahi — taaki reviewer verify kare."""
    if ln.boxes or not ln.words:
        return None
    marks = blank_markers(ln)
    if len(marks) > 1:
        return None  # multiple blanks — normal detect_blanks isko behtar handle karta hai

    blank_x0, blank_x1 = marks[0] if marks else (None, None)
    option_words = [w for w in ln.words if blank_x0 is None or w.x1 <= blank_x0 + 1]
    tail_words = [w for w in ln.words if blank_x0 is not None and w.x0 >= blank_x1 - 1]

    clusters = _cluster_by_gap(option_words, OPTION_GAP_MIN)
    group_label = ""
    if len(clusters) >= 2 and clusters[0][-1].text.rstrip().endswith(":"):
        # Pehla cluster khud ek "Label:" hai (jaise "Food Habits:") — ye ek
        # option nahi hai, poore checkbox-group ka label hai.
        group_label = clean_label(" ".join(w.text for w in clusters[0]))
        clusters = clusters[1:]

    labels = _cluster_texts(clusters)
    if labels is None or len(labels) < 2:
        return None

    comps: List[Component] = [Component(
        type="checkboxgroup",
        label=group_label,
        options=[Option.make(t) for t in labels],
        optionLayout="horizontal" if len(labels) <= 6 else "vertical",
        confidence=0.55,
        source={"page": ln.page, "bbox": list(ln.bbox()), "text": ln.text,
                "note": "checkbox glyph nahi mila — spacing se options infer kiye",
                "needsLabel": not bool(group_label)},
    )]
    if tail_words:
        tail_label = clean_label(" ".join(w.text for w in tail_words))
        if tail_label:
            comps.append(Component(
                type=infer_input_type(tail_label, blank_x1 - blank_x0, page_width),
                label=tail_label[:MAX_LABEL_LEN],
                confidence=0.55,
                source={"page": ln.page, "bbox": list(ln.bbox()), "text": ln.text,
                        "note": "trailing blank after option row"},
            ))
    return comps


# ---------------------------------------------------------------------------
# per-line detectors — har detector (components, confidence) ya None deta hai
# ---------------------------------------------------------------------------
def detect_table(region, page_width: float) -> Component:
    rows = [[(c or "").strip() for c in r] for r in region.rows]
    header = rows[0]
    ncols = max(len(r) for r in rows)
    header = (header + [""] * ncols)[:ncols]
    cols: List[Column] = []
    for i, h in enumerate(header):
        label = clean_label(h) or f"Column {i + 1}"
        cols.append(Column(key=slugify(label, f"col_{i+1}"), label=label, type="textbox"))
    body_rows = rows[1:]
    return Component(
        type="table",
        label="Table",
        columns=cols,
        rowCount=max(1, len(body_rows)),
        confidence=0.8,
        source={"page": region.page, "bbox": list(region.bbox), "rawRows": rows[:6]},
    )


def detect_checkbox_line(ln: TextLine, page_width: float) -> Optional[Component]:
    if not ln.boxes:
        return None
    boxes = ln.boxes
    pre = " ".join(w.text for w in ln.words if w.x1 <= boxes[0].x0 + 1).strip()
    options: List[Option] = []
    for i, b in enumerate(boxes):
        x_end = boxes[i + 1].x0 if i + 1 < len(boxes) else 1e9
        seg = " ".join(
            w.text for w in ln.words
            if w.x0 >= b.x1 - 1 and w.x1 <= x_end + 1
        ).strip()
        seg = clean_label(seg)
        if seg:
            options.append(Option.make(seg))
    if not options:
        return None
    if len(options) == 1 and not pre:
        return Component(
            type="checkbox",
            label=options[0].label,
            confidence=0.75,
            source={"page": ln.page, "bbox": list(ln.bbox()), "text": ln.text},
        )
    label = clean_label(pre)
    return Component(
        type="checkboxgroup",
        label=label,
        options=options,
        optionLayout="horizontal" if len(options) <= 6 else "vertical",
        confidence=0.9 if label else 0.6,
        source={"page": ln.page, "bbox": list(ln.bbox()), "text": ln.text,
                "needsLabel": not bool(label)},
    )


def detect_yesno(ln: TextLine) -> Optional[Component]:
    text = re.sub(r"\s+", " ", ln.text).strip()
    for rx, opts in YESNO_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        label = clean_label(text[: m.start()])
        if not label:
            return None
        return Component(
            type="radiogroup",
            label=label[:MAX_LABEL_LEN],
            options=[Option.make(o) for o in opts],
            optionLayout="horizontal",
            confidence=0.92,
            source={"page": ln.page, "bbox": list(ln.bbox()), "text": text},
        )
    return None


def detect_blanks(ln: TextLine, page_width: float, carry_label: str) -> Optional[List[Component]]:
    marks = blank_markers(ln)
    if not marks:
        return None
    if len(marks) == 1:
        # Blank se pehle wala text agar khud kayi wide-gapped option-phrases mein
        # tootta hai (jaise "Nausea/vomitting  Flushing/Warmth  Other difficulty ___"),
        # to poora text ek hi label maan lena galat hoga — detect_option_row ko
        # is line ko handle karne do (checkbox options + trailing blank field alag).
        pre_words = [w for w in ln.words if w.x1 <= marks[0][0] + 1]
        if _option_clusters(pre_words):
            return None
    comps: List[Component] = []
    cursor = ln.x0 - 1
    for (bx0, bx1) in marks:
        label = clean_label(words_between(ln, cursor, bx0))
        if not label:
            label = carry_label or "Field"
        ctype = infer_input_type(label, bx1 - bx0, page_width)
        comps.append(
            Component(
                type=ctype,
                label=label[:MAX_LABEL_LEN],
                confidence=0.85 if label != "Field" else 0.5,
                source={"page": ln.page, "bbox": list(ln.bbox()), "text": ln.text},
            )
        )
        cursor = bx1
    tail = clean_label(words_between(ln, cursor, 1e9))
    if tail and len(tail) > 2:
        # blank ke baad bacha hua text — aksar agla label hota hai
        comps.append(
            Component(
                type=infer_input_type(tail, 0, page_width),
                label=tail[:MAX_LABEL_LEN],
                confidence=0.55,
                source={"page": ln.page, "bbox": list(ln.bbox()), "text": ln.text,
                        "note": "trailing text after blank"},
            )
        )
    n = len(comps)
    if n > 1:
        w = max(3, 12 // n)
        for c in comps:
            if c.type != "textarea":
                c.width = w
    return comps


def detect_heading(ln: TextLine, page: PageLayout) -> Optional[Component]:
    text = re.sub(r"\s+", " ", ln.text).strip()
    if not text or len(text) > 90:
        return None
    if BLANK_RE.search(text) or ln.boxes:
        return None
    # Font-size sirf tabhi "big text = heading" signal ke liye bharosemand hai
    # jab (a) hum khud OCR se nahi laaye (bbox-height noisy hoti hai), aur
    # (b) is line ke andar size consistent hai (kuch PDFs ka text-layer khud
    # kisi third-party OCR se bana hota hai — wahan bhi size random ghoomti hai).
    size_trustworthy = (not ln.is_ocr) and ln.size_uniform
    big = size_trustworthy and ln.size >= page.body_size * 1.18
    bold = ln.bold_ratio >= 0.6
    upper = looks_upper(text)
    ends_sentence = text.endswith((".", "?", ",", ";"))
    if ends_sentence and not (big or upper):
        return None
    if big or (bold and len(text) <= 70) or (upper and len(text) <= 70):
        htype = "heading" if (size_trustworthy and ln.size >= page.body_size * 1.3) or (big and upper) else "subheading"
        return Component(
            type=htype,
            label=text[:MAX_LABEL_LEN],
            confidence=0.85,
            source={"page": ln.page, "bbox": list(ln.bbox()), "text": text},
        )
    return None


def detect_colon_field(ln: TextLine, page_width: float) -> Optional[Component]:
    text = re.sub(r"\s+", " ", ln.text).strip()
    if not text.endswith(":"):
        return None
    label = clean_label(text)
    if not label:
        return None
    return Component(
        type=infer_input_type(label, 0, page_width),
        label=label[:MAX_LABEL_LEN],
        confidence=0.8,
        source={"page": ln.page, "bbox": list(ln.bbox()), "text": text},
    )


def detect_question(ln: TextLine, page_width: float) -> Optional[Component]:
    text = re.sub(r"\s+", " ", ln.text).strip()
    if not text.endswith("?"):
        return None
    label = clean_label(text)
    return Component(
        type=infer_input_type(label, 0, page_width),
        label=label[:MAX_LABEL_LEN],
        confidence=0.7,
        source={"page": ln.page, "bbox": list(ln.bbox()), "text": text},
    )


# ---------------------------------------------------------------------------
# main pass
# ---------------------------------------------------------------------------
def _merge_wrapped_options(comps: List[Component], comp: Component) -> bool:
    """'Local' jaisa wrapped checkbox option pichhle group mein hi jod do."""
    if not comps:
        return False
    prev = comps[-1]
    if prev.type != "checkboxgroup" or comp.type not in ("checkboxgroup", "checkbox"):
        return False
    if comp.type == "checkboxgroup" and comp.label:
        return False
    new_opts = comp.options if comp.options else [Option.make(comp.label)]
    prev.options.extend(new_opts)
    if len(prev.options) > 6:
        prev.optionLayout = "vertical"
    return True


def _promote_heading_label(comps: List[Component], comp: Component) -> None:
    """Heading ke turant baad aaya bina-label checkbox group -> heading ko uska label bana do."""
    if comp.type != "checkboxgroup" or comp.label:
        return
    if not comps:
        return
    prev = comps[-1]
    if prev.type in ("heading", "subheading"):
        comp.label = prev.label
        comp.key = slugify(prev.label, "checkbox_group")
        comp.confidence = max(comp.confidence, 0.85)
        comp.source["labelFrom"] = "previous heading"
        comps.pop()


def _postprocess(components: List[Component]) -> List[Component]:
    """Poore document ko dekh kar chhoti galtiyan sudharo (lookahead rules)."""
    CHOICE = {"radiogroup", "checkboxgroup", "checkbox", "dropdown"}

    for i, c in enumerate(components):
        # A) "Do you have any of the following conditions?" jaisa line jiske neeche
        #    indented sub-questions hain -> wo input nahi, section header hai.
        if c.type not in ("textbox", "textarea"):
            continue
        src_x = (c.source or {}).get("bbox", [None])[0]
        if src_x is None:
            continue
        followers = []
        for nxt in components[i + 1:]:
            nx = (nxt.source or {}).get("bbox", [None])[0]
            if nxt.type in CHOICE and nx is not None and nx > src_x + 6:
                followers.append(nxt)
            else:
                break
        if len(followers) >= 2:
            c.type = "subheading"
            c.options = []
            c.confidence = 0.8
            c.source["note"] = "converted: header for indented sub-questions"

    # B) lagatar paragraphs ko ek hi block mein jodo
    merged: List[Component] = []
    for c in components:
        if merged and c.type == "paragraph" and merged[-1].type == "paragraph":
            merged[-1].label = f"{merged[-1].label} {c.label}".strip()[:900]
            continue
        merged.append(c)
    return merged


OCR_CONFIDENCE_CAP = 0.65


def _mark_ocr(comp: Component, ln: TextLine) -> Component:
    """OCR se aayi line ka component hamesha review-worthy maanenge — geometry
    rules jitna precise nahi ho sakta jab underlying text hi OCR se aaya ho."""
    if ln.is_ocr:
        comp.confidence = min(comp.confidence, OCR_CONFIDENCE_CAP)
        comp.detectedBy = "rule-ocr"
    return comp


def classify_pages(pages: List[PageLayout], title: str = "") -> Template:
    components: List[Component] = []
    carry_label = ""
    pending_paragraph: List[str] = []

    def flush_paragraph():
        nonlocal pending_paragraph
        if pending_paragraph:
            text = " ".join(pending_paragraph).strip()
            if len(text) > 1:
                components.append(
                    Component(type="paragraph", label=text[:600], confidence=0.6,
                              source={"kind": "narrative"})
                )
            pending_paragraph = []

    for page in pages:
        # tables ko document order mein merge karne ke liye ek combined stream
        stream: List[Tuple[float, str, object]] = []
        for ln in page.lines:
            stream.append((ln.top, "line", ln))
        for t in page.tables:
            stream.append((t.bbox[1], "table", t))
        stream.sort(key=lambda x: x[0])

        for _, kind, obj in stream:
            if kind == "table":
                flush_paragraph()
                comp = detect_table(obj, page.width)
                if page.is_ocr:
                    # Sparse-text page pe table cell text pdfplumber ke asli
                    # chars se aata hai (OCR se nahi) — is page pe wo bharosemand
                    # nahi, isliye review ke liye flag karo.
                    comp.confidence = min(comp.confidence, OCR_CONFIDENCE_CAP)
                    comp.detectedBy = "rule-ocr"
                if components and components[-1].type in ("heading", "subheading"):
                    comp.label = components[-1].label
                    comp.key = slugify(comp.label, "table")
                    components.pop()
                components.append(comp)
                continue

            ln: TextLine = obj
            text = re.sub(r"\s+", " ", ln.text).strip()
            if not text and not ln.boxes and not ln.rules:
                continue

            # 1) checkbox row
            comp = detect_checkbox_line(ln, page.width)
            if comp:
                _mark_ocr(comp, ln)
                flush_paragraph()
                if _merge_wrapped_options(components, comp):
                    continue
                _promote_heading_label(components, comp)
                if not comp.label:
                    comp.label = carry_label or "Options"
                    comp.key = slugify(comp.label, "options")
                components.append(comp)
                carry_label = comp.label
                continue

            # 2) Yes / No style choice
            comp = detect_yesno(ln)
            if comp:
                _mark_ocr(comp, ln)
                flush_paragraph()
                components.append(comp)
                carry_label = comp.label
                continue

            # 3) fill-in-the-blank(s)
            blanks = detect_blanks(ln, page.width, carry_label)
            if blanks:
                for b in blanks:
                    _mark_ocr(b, ln)
                flush_paragraph()
                components.extend(blanks)
                carry_label = blanks[-1].label
                continue

            # 4) heading
            comp = detect_heading(ln, page)
            if comp:
                _mark_ocr(comp, ln)
                flush_paragraph()
                components.append(comp)
                carry_label = comp.label
                continue

            # 5) "Label:" field
            comp = detect_colon_field(ln, page.width)
            if comp:
                _mark_ocr(comp, ln)
                flush_paragraph()
                components.append(comp)
                carry_label = comp.label
                continue

            # 6) question mark field
            comp = detect_question(ln, page.width)
            if comp:
                _mark_ocr(comp, ln)
                flush_paragraph()
                components.append(comp)
                carry_label = comp.label
                continue

            # 7) wide-gapped options bina checkbox glyph ke (jaise "CT Scan   IVP   Angiogram")
            opt_comps = detect_option_row(ln, page.width)
            if opt_comps:
                for c in opt_comps:
                    _mark_ocr(c, ln)
                flush_paragraph()
                group = opt_comps[0]
                rest = opt_comps[1:]
                # Sirf tabhi pichhli group mein merge karo jab is line mein
                # kahin colon hi nahi hai — matlab ye structurally kabhi apna
                # label rakh hi nahi sakti thi (genuine wrapped continuation).
                # Colon maujood hai par label clustering miss ho gaya ho, to
                # merge mat karo — warna do alag groups galat se jud jaate hain.
                if group.type == "checkboxgroup" and ":" not in ln.text and _merge_wrapped_options(components, group):
                    # Wrapped continuation (jaise "Past Medical History" ki
                    # dusri line) — nayi group nahi, pichhli mein hi jod do.
                    pass
                else:
                    if group.type == "checkboxgroup" and not group.label:
                        group.label = carry_label or "Options"
                        group.key = slugify(group.label, "options")
                    components.append(group)
                components.extend(rest)
                carry_label = (rest[-1] if rest else group).label
                continue

            # 8) narrative text
            pending_paragraph.append(text)

        flush_paragraph()

    components = _postprocess(components)

    # duplicate keys unique karo
    seen: Dict[str, int] = {}
    for c in components:
        base = c.key or slugify(c.label, c.type)
        n = seen.get(base, 0)
        seen[base] = n + 1
        c.key = base if n == 0 else f"{base}_{n+1}"

    if not title:
        title = next((c.label for c in components if c.type == "heading"), "Imported Form")

    tpl = Template(title=title, components=components,
                   meta={"pages": len(pages), "generator": "formforge-rules"})
    return tpl.renumber()


def pdf_to_template(path: str, title: str = "") -> Template:
    pages = extract_pdf(path)
    return classify_pages(pages, title=title)
