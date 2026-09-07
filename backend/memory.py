"""
Correction memory — ek badhta hua pattern store, ML training NAHI.

Jab user template final save karta hai, local-parse output ("original") aur
uska final corrected version diff kiye jaate hain, aur har correction yahan
store hota hai — teen tarah se:

  a) line_map     — poori line ka normalized text -> final {type, label, options}.
                     Hospital forms mein ek hi line dozens forms mein repeat hoti
                     hai, isliye ye sabse bada win hai.
  b) keyword_map   — ek significant keyword -> type (jaise "creatinine" -> number).
  c) patterns      — naya structural pattern: line ke end mein kuch options-jaisa
                     text ho aur user ne use radiogroup/checkboxgroup banaya ho
                     (jaise "Yes No" ka rule already classify.py mein hardcoded
                     hai — ye wahi cheez seekh kar generalize karta hai).

Apply karne ke rules (`apply_memory`):
  - Rules ke BAAD, final output se PEHLE chalta hai (classify.py ko chhuta nahi).
  - Koi bhi entry sirf tab auto-apply hoti hai jab kam se kam 2 baar dekhi gayi ho.
  - Apply hone par confidence hamesha < 0.8 rakhi jaati hai — reviewer ko pata
    rahe ki ye ek "learned guess" hai, geometry-certain nahi.
  - heading/subheading/paragraph/divider/table components ko keyword/pattern
    memory kabhi override nahi karti (sirf exact line_map match karti hai) —
    warna ek heading mein galti se koi seekha hua keyword aa kar use input
    field bana sakta hai.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from .schema import Component, Option, Template, slugify

STORE_PATH = Path(__file__).resolve().parent.parent / "storage" / "memory.json"
MIN_SEEN_TO_APPLY = 2
MEMORY_CONFIDENCE = 0.75  # hamesha < 0.8

STRUCTURAL_TYPES = {"heading", "subheading", "paragraph", "divider", "table", "fieldset"}

STOPWORDS = {
    "the", "a", "an", "of", "is", "if", "any", "your", "please", "list", "and",
    "or", "for", "to", "in", "on", "at", "with", "this", "that", "are", "you",
    "have", "has", "do", "does", "did", "be", "was", "were", "will", "would",
    "should", "can", "could", "not", "no", "yes", "all", "other", "specify",
    "details", "information", "from", "into", "than", "then", "also",
}

_LEAD_NUM_RE = re.compile(r"^\s*(\d{1,2}\s*[.)]|[a-hA-H]\s*[.)]|[ivxIVX]{1,4}\s*[.)])\s*")


def _normalize_line(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = _LEAD_NUM_RE.sub("", t)
    return t.strip(" .:;-–—").lower()


def _significant_words(text: str, limit: int = 4) -> List[str]:
    words = re.findall(r"[a-zA-Z]{4,}", (text or "").lower())
    out: List[str] = []
    for w in words:
        if w not in STOPWORDS and w not in out:
            out.append(w)
        if len(out) >= limit:
            break
    return out


def _empty_store() -> Dict[str, Any]:
    return {"line_map": {}, "keyword_map": {}, "patterns": []}


def _load() -> Dict[str, Any]:
    if not STORE_PATH.exists():
        return _empty_store()
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_store()
    for key in ("line_map", "keyword_map", "patterns"):
        data.setdefault(key, {} if key != "patterns" else [])
    return data


def _save(data: Dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _touch_map_entry(bucket: Dict[str, Any], key: str, value: Dict[str, Any], author: str) -> bool:
    """Entry add/refresh karta hai. True dega agar seen-count badha (naya
    ya reinforced observation), taaki caller "kitna seekha" report kar sake."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = bucket.get(key)
    if entry and entry.get("type") == value.get("type"):
        entry["seen"] = entry.get("seen", 1) + 1
        entry["lastSeen"] = now
        if value.get("label"):
            entry["label"] = value["label"]
        if value.get("options"):
            entry["options"] = value["options"]
        return True
    bucket[key] = {**value, "seen": 1, "createdAt": now, "lastSeen": now, "createdBy": author}
    return True


def learn_from_correction(
    original: List[Dict[str, Any]],
    corrected: List[Dict[str, Any]],
    author: str = "local-user",
) -> Dict[str, int]:
    """`original` (parse ke turant baad ka output) vs `corrected` (user ne
    final save kiya) compare karke memory update karta hai. Best-effort hai —
    kabhi exception raise nahi karta, save flow kabhi isse fail nahi hoga."""
    report = {"lineLearned": 0, "keywordLearned": 0, "patternLearned": 0}
    try:
        data = _load()
        orig_by_id = {c.get("id"): c for c in original if c.get("id")}

        for c in corrected:
            o = orig_by_id.get(c.get("id"))
            if not o:
                continue  # user ne khud naya field add kiya — "original galti" nahi hai

            o_opts = [x.get("label", "") for x in o.get("options", [])]
            c_opts = [x.get("label", "") for x in c.get("options", [])]
            if (o.get("type") == c.get("type")
                    and (o.get("label") or "") == (c.get("label") or "")
                    and o_opts == c_opts):
                continue  # kuch badla hi nahi

            ctype = c.get("type", "textbox")
            clabel = (c.get("label") or "").strip()
            src_text = (o.get("source") or {}).get("text") or o.get("label") or ""
            norm = _normalize_line(src_text)

            # a) line text -> component
            if norm:
                if _touch_map_entry(
                    data["line_map"], norm,
                    {"type": ctype, "label": clabel, "options": c_opts}, author,
                ):
                    report["lineLearned"] += 1

            # b) keyword -> type (sirf typed input-fields ke liye meaningful)
            if ctype in ("number", "date", "time", "signature", "fileupload", "textarea"):
                for kw in _significant_words(clabel or src_text):
                    if _touch_map_entry(data["keyword_map"], kw, {"type": ctype}, author):
                        report["keywordLearned"] += 1

            # c) naya structural pattern — line ke end mein options-jaisa suffix
            if ctype in ("radiogroup", "checkboxgroup") and c_opts and norm:
                lowered_opts = [o_.lower() for o_ in c_opts if o_]
                if lowered_opts and all(o_ in norm for o_ in lowered_opts):
                    suffix = " ".join(lowered_opts)
                    now = time.strftime("%Y-%m-%d %H:%M:%S")
                    existing = next((p for p in data["patterns"] if p.get("suffix") == suffix), None)
                    if existing:
                        existing["seen"] = existing.get("seen", 1) + 1
                        existing["lastSeen"] = now
                    else:
                        data["patterns"].append({
                            "suffix": suffix, "type": ctype, "options": c_opts,
                            "seen": 1, "createdAt": now, "lastSeen": now, "createdBy": author,
                        })
                        report["patternLearned"] += 1

        _save(data)
    except Exception:
        pass  # memory kabhi bhi save-flow ko todegi nahi
    return report


def _apply_line_map(c: Component, norm: str, line_map: Dict[str, Any]) -> bool:
    entry = line_map.get(norm)
    if not entry or entry.get("seen", 0) < MIN_SEEN_TO_APPLY:
        return False
    c.type = entry["type"]
    if entry.get("label"):
        c.label = entry["label"]
    if entry.get("options"):
        c.options = [Option.make(o) for o in entry["options"]]
    c.key = slugify(c.label, c.type)
    return True


def _apply_pattern(c: Component, norm: str, patterns: List[Dict[str, Any]]) -> bool:
    for pat in patterns:
        if pat.get("seen", 0) < MIN_SEEN_TO_APPLY:
            continue
        opts = pat.get("options", [])
        if not opts or not norm:
            continue
        if all(o.lower() in norm for o in opts):
            c.type = pat["type"]
            c.options = [Option.make(o) for o in opts]
            c.key = slugify(c.label, c.type)
            return True
    return False


def _apply_keyword(c: Component, keyword_map: Dict[str, Any]) -> bool:
    for kw in _significant_words(c.label):
        entry = keyword_map.get(kw)
        if entry and entry.get("seen", 0) >= MIN_SEEN_TO_APPLY and entry.get("type") != c.type:
            c.type = entry["type"]
            return True
    return False


def apply_memory(template: Template) -> Dict[str, int]:
    """Har component par (rules chal chukne ke baad) learned corrections try
    karta hai. Sirf line_map heading/table jaise static components pe bhi
    lagu hoti hai (exact-match hai, safe hai); pattern/keyword sirf already
    input-jaisa dikhne wale components pe (galti se heading ko field na banaye)."""
    data = _load()
    line_map, keyword_map, patterns = data["line_map"], data["keyword_map"], data["patterns"]
    report = {"reviewed": len(template.components), "applied": 0}

    for c in template.components:
        src_text = (c.source or {}).get("text") or c.label or ""
        norm = _normalize_line(src_text)
        applied = _apply_line_map(c, norm, line_map)

        if not applied and c.type not in STRUCTURAL_TYPES:
            applied = _apply_pattern(c, norm, patterns) or _apply_keyword(c, keyword_map)

        if applied:
            c.confidence = min(c.confidence, MEMORY_CONFIDENCE)
            c.detectedBy = "memory"
            report["applied"] += 1

    return report


def top_examples(limit: int = 8) -> List[Dict[str, Any]]:
    """Sabse zyada dekhe gaye line_map corrections — AI Parse (Gemini) ke
    prompt mein few-shot examples ki tarah bhejne ke liye. Sirf wahi jo
    already MIN_SEEN_TO_APPLY paar kar chuke hain (matlab genuinely repeat
    hua pattern hai, ek-baar ka fluke nahi)."""
    data = _load()
    items = [
        {"line": k, "type": v.get("type"), "label": v.get("label"), "options": v.get("options", []),
         "seen": v.get("seen", 0)}
        for k, v in data.get("line_map", {}).items()
        if v.get("seen", 0) >= MIN_SEEN_TO_APPLY
    ]
    items.sort(key=lambda x: -x["seen"])
    return items[:limit]
