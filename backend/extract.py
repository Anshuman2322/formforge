"""
Layer 1 — PDF se raw layout nikaalna (koi interpretation nahi, sirf geometry).

pdfplumber deta hai: words + coordinates, stroked lines, rects, aur ruled tables.
Yahan hum unhe visual "text lines" mein group karte hain aur checkbox-jaise
chhote squares + underline rules alag maark karte hain.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

from .ocr_fallback import ocr_words, page_is_sparse

# ---------------------------------------------------------------------------
# tuning constants
# ---------------------------------------------------------------------------
LINE_TOL = 3.0          # same visual line maanne ke liye vertical tolerance (pt)
CHECKBOX_MIN = 4.0      # checkbox square ka min side
CHECKBOX_MAX = 18.0     # max side
CHECKBOX_SQUARENESS = 4.0
RULE_MIN_LEN = 18.0     # itni lambi horizontal line ko "blank" maanenge
RULE_MAX_THICK = 2.5


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    fontname: str

    @property
    def bold(self) -> bool:
        f = (self.fontname or "").lower()
        return "bold" in f or "black" in f or ",b" in f or "-b" in f


@dataclass
class Box:
    """Checkbox-jaisa chhota square ya koi bhi rect."""
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Rule:
    """Horizontal line — aksar 'fill in the blank' ka underline."""
    x0: float
    x1: float
    y: float

    @property
    def length(self) -> float:
        return self.x1 - self.x0


@dataclass
class TextLine:
    page: int
    words: List[Word]
    boxes: List[Box] = field(default_factory=list)     # is line pe baithe checkboxes
    rules: List[Rule] = field(default_factory=list)    # is line pe baithe underlines

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words).strip()

    @property
    def top(self) -> float:
        return min((w.top for w in self.words), default=0.0)

    @property
    def bottom(self) -> float:
        return max((w.bottom for w in self.words), default=0.0)

    @property
    def x0(self) -> float:
        return min((w.x0 for w in self.words), default=0.0)

    @property
    def x1(self) -> float:
        return max((w.x1 for w in self.words), default=0.0)

    @property
    def size(self) -> float:
        sizes = [w.size for w in self.words if w.size]
        return statistics.median(sizes) if sizes else 0.0

    @property
    def bold_ratio(self) -> float:
        if not self.words:
            return 0.0
        return sum(1 for w in self.words if w.bold) / len(self.words)

    @property
    def size_uniform(self) -> bool:
        """Real headings mein poori line ka font-size ek jaisa hota hai
        (~1-1.5pt tak). Kuch PDFs ka text-layer khud kisi third-party OCR
        tool se bana hota hai (fontname jaise 'Untitled', per-word size
        random ghoomti rehti hai) — aisi line ka size "big" dikh sakta hai
        sirf noise se, heading hone se nahi."""
        sizes = [w.size for w in self.words if w.size]
        if len(sizes) < 2:
            return True
        return (max(sizes) - min(sizes)) <= 2.5

    def bbox(self) -> Tuple[float, float, float, float]:
        return (self.x0, self.top, self.x1, self.bottom)

    @property
    def is_ocr(self) -> bool:
        return any(w.fontname == "ocr" for w in self.words)


@dataclass
class TableRegion:
    page: int
    bbox: Tuple[float, float, float, float]
    rows: List[List[Optional[str]]]


@dataclass
class PageLayout:
    number: int
    width: float
    height: float
    lines: List[TextLine]
    tables: List[TableRegion]
    body_size: float          # sabse common font size = body text
    max_size: float
    is_ocr: bool = False      # is page ke words Tesseract se aaye (sparse text layer)


def _is_checkbox(rect: Dict[str, Any]) -> bool:
    w = abs(rect["x1"] - rect["x0"])
    h = abs(rect["bottom"] - rect["top"])
    return (
        CHECKBOX_MIN <= w <= CHECKBOX_MAX
        and CHECKBOX_MIN <= h <= CHECKBOX_MAX
        and abs(w - h) <= CHECKBOX_SQUARENESS
    )


def _collect_boxes(page, include_curves: bool = True) -> List[Box]:
    """`page.curves` mein sirf tab checkbox-jaisi shapes dhoondo jab bharosemand
    ho — kyunki jab page ka text font-glyphs ki jagah vector curves mein
    "outline" kiya gaya ho (flattened-text PDFs), to har LETTER khud ek curve
    hota hai, aur unke bounding boxes aksar 4-18pt ke squarish hote hain — wo
    checkbox samajh liye jaate agar hum yahan curves bhi scan karte."""
    boxes: List[Box] = []
    source = list(page.rects) + (list(page.curves) if include_curves else [])
    for r in source:
        try:
            if _is_checkbox(r):
                boxes.append(Box(r["x0"], r["x1"], r["top"], r["bottom"]))
        except (KeyError, TypeError):
            continue
    # duplicates hata do (stroke + fill do rects de sakta hai)
    uniq: List[Box] = []
    for b in boxes:
        if not any(abs(b.x0 - u.x0) < 1.5 and abs(b.top - u.top) < 1.5 for u in uniq):
            uniq.append(b)
    return uniq


def _collect_rules(page) -> List[Rule]:
    rules: List[Rule] = []
    for ln in page.lines:
        if abs(ln["top"] - ln["bottom"]) <= RULE_MAX_THICK and (ln["x1"] - ln["x0"]) >= RULE_MIN_LEN:
            rules.append(Rule(ln["x0"], ln["x1"], (ln["top"] + ln["bottom"]) / 2))
    # bahut patli rects bhi underline ho sakti hain
    for r in page.rects:
        h = abs(r["bottom"] - r["top"])
        w = abs(r["x1"] - r["x0"])
        if h <= RULE_MAX_THICK and w >= RULE_MIN_LEN:
            rules.append(Rule(r["x0"], r["x1"], (r["top"] + r["bottom"]) / 2))
    return rules


def _cluster_by_gap(words: List[Word], gap_min: float) -> List[List[Word]]:
    """Words ko x-gap se sub-groups mein todta hai (ek row ke andar bhi)."""
    if not words:
        return []
    clusters: List[List[Word]] = [[words[0]]]
    for w in words[1:]:
        prev = clusters[-1][-1]
        if w.x0 - prev.x1 >= gap_min:
            clusters.append([w])
        else:
            clusters[-1].append(w)
    return clusters


COLUMN_GAP_MIN = 20.0  # itna x-gap ho AUR dono taraf apna khud ka "Label:" ho, tabhi column-split maano


def _looks_like_label(cluster: List[Word]) -> bool:
    return bool(cluster) and cluster[-1].text.rstrip().endswith(":")


def _split_label_columns(line: TextLine) -> List[TextLine]:
    """Multi-column form layouts mein ek hi visual row pe do (ya zyada)
    independent 'Label:' field hote hain (jaise 'Name: ____   Height: ____').
    Purani _group_lines sirf Y-proximity dekhti hai, x-gap nahi — isliye aisi
    rows ek hi TextLine ban jaati thi (jaise "Name: Height" ek hi field). Yahan
    sirf tabhi split karte hain jab gap ke DONO taraf apna khud ka colon-terminated
    label ho — checkbox-option rows (jahan pehla cluster label hai, baaki plain
    option-words) isse untouched rehte hain, kyunki unka doosra cluster label
    jaisa nahi dikhta."""
    if len(line.words) < 2:
        return [line]
    clusters = _cluster_by_gap(line.words, COLUMN_GAP_MIN)
    if len(clusters) < 2:
        return [line]
    split_at = [
        i + 1 for i in range(len(clusters) - 1)
        if _looks_like_label(clusters[i]) and _looks_like_label(clusters[i + 1])
    ]
    if not split_at:
        return [line]
    groups: List[List[Word]] = []
    start = 0
    for idx in split_at:
        groups.append([w for c in clusters[start:idx] for w in c])
        start = idx
    groups.append([w for c in clusters[start:] for w in c])
    return [TextLine(page=line.page, words=g) for g in groups if g]


def _group_lines(words: List[Word], page_no: int) -> List[TextLine]:
    """Words ko visual lines mein group karo (top coordinate ke hisaab se)."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (round(w.top, 1), w.x0))
    lines: List[TextLine] = []
    current: List[Word] = [words[0]]
    for w in words[1:]:
        ref = current[-1]
        if abs(w.top - ref.top) <= LINE_TOL or (w.top < ref.bottom - 1 and w.bottom > ref.top + 1):
            current.append(w)
        else:
            lines.append(TextLine(page=page_no, words=sorted(current, key=lambda x: x.x0)))
            current = [w]
    lines.append(TextLine(page=page_no, words=sorted(current, key=lambda x: x.x0)))
    return sorted(lines, key=lambda l: (l.top, l.x0))


def _attach_marks(lines: List[TextLine], boxes: List[Box], rules: List[Rule]) -> None:
    """Checkbox squares aur underlines ko unke nearest text line se jodo."""
    for b in boxes:
        best, best_d = None, 1e9
        for ln in lines:
            # vertical center ke hisaab se
            if b.cy >= ln.top - 6 and b.cy <= ln.bottom + 6:
                d = abs(b.cy - (ln.top + ln.bottom) / 2)
                if d < best_d:
                    best, best_d = ln, d
        if best is not None:
            best.boxes.append(b)
    for r in rules:
        best, best_d = None, 1e9
        for ln in lines:
            # underline baseline ke thoda neeche hota hai
            d = r.y - ln.bottom
            if -3 <= d <= 6:
                if abs(d) < best_d:
                    best, best_d = ln, abs(d)
        if best is not None:
            best.rules.append(r)
    for ln in lines:
        ln.boxes.sort(key=lambda b: b.x0)
        ln.rules.sort(key=lambda r: r.x0)


def _find_tables(page, page_no: int) -> List[TableRegion]:
    regions: List[TableRegion] = []
    try:
        found = page.find_tables()
    except Exception:
        return regions
    for t in found:
        try:
            rows = t.extract()
        except Exception:
            continue
        if not rows or len(rows) < 2:
            continue
        ncols = max(len(r) for r in rows)
        if ncols < 2:
            continue
        regions.append(TableRegion(page=page_no, bbox=tuple(t.bbox), rows=rows))
    return regions


def _inside(bbox: Tuple[float, float, float, float], line: TextLine, pad: float = 2.0) -> bool:
    x0, top, x1, bottom = bbox
    cx = (line.x0 + line.x1) / 2
    cy = (line.top + line.bottom) / 2
    return x0 - pad <= cx <= x1 + pad and top - pad <= cy <= bottom + pad


def extract_pdf(path: str, max_pages: int = 30) -> List[PageLayout]:
    layouts: List[PageLayout] = []
    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages[:max_pages], start=1):
            raw_words = page.extract_words(
                use_text_flow=False,
                keep_blank_chars=False,
                extra_attrs=["size", "fontname"],
            )
            words = [
                Word(
                    text=w["text"],
                    x0=w["x0"], x1=w["x1"], top=w["top"], bottom=w["bottom"],
                    size=float(w.get("size") or 0.0),
                    fontname=str(w.get("fontname") or ""),
                )
                for w in raw_words
            ]
            char_count = sum(len(w.text) for w in words)
            sparse = page_is_sparse(char_count, page)
            if sparse:
                # Text layer bharosemand nahi (scan, ya glyphs vector curves mein
                # flatten ho gaye) — Tesseract se words nikalo, baaki pipeline
                # (checkbox/rule/table geometry, classify.py ke rules) same rehti hai.
                words = ocr_words(page)

            lines = _group_lines(words, idx)
            split: List[TextLine] = []
            for ln in lines:
                split.extend(_split_label_columns(ln))
            lines = sorted(split, key=lambda l: (l.top, l.x0))
            # sparse page pe curves letter-glyphs ho sakte hain, checkbox nahi —
            # sirf real page.rects se checkbox dhoondo.
            boxes = _collect_boxes(page, include_curves=not sparse)
            rules = _collect_rules(page)
            _attach_marks(lines, boxes, rules)
            tables = _find_tables(page, idx)

            # table ke andar ki lines ko hata do — wo table component ban chuki hain
            if tables:
                lines = [
                    ln for ln in lines
                    if not any(_inside(t.bbox, ln) for t in tables)
                ]

            sizes = [round(w.size, 1) for w in words if w.size]
            body = statistics.mode(sizes) if sizes else 10.0
            layouts.append(
                PageLayout(
                    number=idx,
                    width=float(page.width),
                    height=float(page.height),
                    lines=lines,
                    tables=tables,
                    body_size=body,
                    max_size=max(sizes) if sizes else body,
                    is_ocr=sparse,
                )
            )
    return layouts
