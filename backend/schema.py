"""
FormForge neutral form schema.

Ye schema deliberately form.io / KareXpert template-builder ke close rakha gaya hai,
taaki baad mein ek chhota adapter likh ke KareXpert JSON export kiya ja sake.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Component types
# ---------------------------------------------------------------------------
# Har type ka builder UI mein ek renderer hai (frontend/index.html dekhein).
COMPONENT_TYPES = [
    "heading",        # bada section title
    "subheading",     # chhota section title
    "paragraph",      # static text / instructions (rich text)
    "textbox",        # single line input
    "textarea",       # multi line input
    "number",         # numeric input
    "date",           # date picker
    "time",           # time picker
    "dropdown",       # select
    "radiogroup",     # single choice
    "checkboxgroup",  # multi choice
    "checkbox",       # single boolean
    "table",          # grid / tabular data
    "signature",      # signature pad
    "fileupload",     # attachment
    "fieldset",       # container (children)
    "divider",
]

CHOICE_TYPES = {"dropdown", "radiogroup", "checkboxgroup"}
TEXT_ONLY_TYPES = {"heading", "subheading", "paragraph", "divider"}


def new_id(prefix: str = "c") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def slugify(text: str, fallback: str = "field") -> str:
    """Label ko machine-friendly key mein badalta hai."""
    text = re.sub(r"[^\w\s-]", " ", text or "", flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return fallback
    return text[:60]


@dataclass
class Option:
    id: str
    label: str

    @staticmethod
    def make(label: str) -> "Option":
        return Option(id=slugify(label, "opt"), label=label.strip())


@dataclass
class Column:
    key: str
    label: str
    type: str = "textbox"
    width: int = 0  # 0 = auto


@dataclass
class Component:
    type: str
    label: str = ""
    key: str = ""
    id: str = field(default_factory=lambda: new_id())

    # generic props
    placeholder: str = ""
    hint: str = ""
    required: bool = False
    readonly: bool = False
    defaultValue: str = ""
    width: int = 12                 # 12-column grid
    order: int = 0

    # choice props
    options: List[Option] = field(default_factory=list)
    optionLayout: str = "horizontal"   # horizontal | vertical

    # textarea
    rows: int = 3

    # number
    minValue: Optional[float] = None
    maxValue: Optional[float] = None
    unit: str = ""

    # table
    columns: List[Column] = field(default_factory=list)
    rowCount: int = 3
    allowAddRows: bool = True

    # container
    children: List["Component"] = field(default_factory=list)

    # provenance — builder UI mein "AI ne ye kahan se nikala" dikhane ke liye
    confidence: float = 1.0
    detectedBy: str = "rule"          # rule | llm | manual
    source: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.key:
            self.key = slugify(self.label, self.type)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class Template:
    title: str = "Untitled Template"
    schemaVersion: str = SCHEMA_VERSION
    id: str = field(default_factory=lambda: new_id("tpl"))
    description: str = ""
    components: List[Component] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def renumber(self) -> "Template":
        for i, c in enumerate(self.components, start=1):
            c.order = i
        return self

    def to_dict(self) -> Dict[str, Any]:
        self.renumber()
        return {
            "id": self.id,
            "schemaVersion": self.schemaVersion,
            "title": self.title,
            "description": self.description,
            "meta": self.meta,
            "components": [c.to_dict() for c in self.components],
        }


# ---------------------------------------------------------------------------
# Adapter stub: neutral schema -> KareXpert / form.io style
# ---------------------------------------------------------------------------
_KX_TYPE_MAP = {
    "heading": "subheading",
    "subheading": "subheading",
    "paragraph": "htmlelement",
    "textbox": "textfield",
    "textarea": "textarea",
    "number": "number",
    "date": "datetime",
    "time": "time",
    "dropdown": "select",
    "radiogroup": "radio",
    "checkboxgroup": "selectboxes",
    "checkbox": "checkbox",
    "table": "datagrid",
    "signature": "signature",
    "fileupload": "file",
    "fieldset": "fieldset",
    "divider": "content",
}


def to_formio(template: Template) -> Dict[str, Any]:
    """Neutral schema -> form.io-ish components array.

    KareXpert ka builder isi family ka hai; exact field names milne par
    yahi function tweak karna hoga, baaki pipeline same rahegi.
    """

    def conv(c: Component) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": _KX_TYPE_MAP.get(c.type, "textfield"),
            "key": c.key,
            "label": c.label,
            "input": c.type not in TEXT_ONLY_TYPES,
            "validate": {"required": c.required},
            "customClass": f"col-lg-{c.width} col-md-{c.width} col-sm-12",
        }
        if c.type in CHOICE_TYPES:
            out["data"] = {"values": [{"value": o.id, "label": o.label} for o in c.options]}
            out["inline"] = c.optionLayout == "horizontal"
        if c.type == "table":
            out["components"] = [
                {"type": _KX_TYPE_MAP.get(col.type, "textfield"), "key": col.key, "label": col.label}
                for col in c.columns
            ]
        if c.type == "fieldset":
            out["components"] = [conv(ch) for ch in c.children]
        if c.type in ("paragraph", "divider"):
            out["html"] = c.label
        return out

    template.renumber()
    return {"title": template.title, "display": "form",
            "components": [conv(c) for c in template.components]}
