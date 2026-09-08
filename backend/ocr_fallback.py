"""
Layer 1b (fallback) — jab PDF ke page ka text layer extract na ho (scan, ya
font glyphs vector curves mein "outlined"/flatten kiye gaye), Tesseract
(free, open-source, fully offline OCR engine) se page image se words
nikalte hain — bilkul unhi `Word` objects ki tarah jo digital-text PDFs se
milte hain. Ye words phir wahi geometry pipeline (`_group_lines`,
checkbox/rule attach, classify.py ke detectors) se guzarte hain jo normal
digital PDFs pe chalti hai — koi alag "AI schema" nahi banta, same rules
reuse hoti hain.

Koi API key ya paid service ki zaroorat nahi. Sirf Tesseract-OCR engine
system par installed hona chahiye:
    winget install --id UB-Mannheim.TesseractOCR -e
    (ya https://github.com/UB-Mannheim/tesseract/wiki se manually)
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING, Any, List

if TYPE_CHECKING:
    from .extract import Word

# Extracted text itna kam ho (chars, na ki "words" — rotated/decorative glyphs
# often ek-ek letter ka alag "word" ban jaate hain) aur visual "ink"
# (curves/rects/lines/image) itni zyada ho to matlab text layer bharosemand
# nahi hai — scan ya outline-as-curves PDF.
SPARSE_CHAR_MAX = 40
SPARSE_INK_MIN = 40

# dpi — jitna zyada, OCR accuracy utni behtar, par render+recognize dono
# resolution^2 ke hisaab se slow hote hain, aur constrained hosting (jaise
# Render free tier ka 0.1 CPU) pe ye already-minutes-long OCR ko aur lamba
# kar deta hai. Benchmarked (samples/real_hinduja_form.pdf, local CPU):
# 200dpi->295 words/1.45s, 150dpi->297/1.01s, 120dpi->294/0.88s, 100dpi->271/0.84s
# — accuracy 120dpi tak flat rehti hai, 100dpi pe real drop shuru hota hai.
# 120 ko safe floor maana — ~40% kam kaam 200dpi ke muqable, koi accuracy loss nahi
# (is ek sample pe). FORMFORGE_OCR_RESOLUTION se override karo agar zaroorat ho
# (kam-constrained host ho to upar bhi le ja sakte ho behtar accuracy ke liye).
OCR_RESOLUTION = int(os.environ.get("FORMFORGE_OCR_RESOLUTION", "120"))
MIN_CONFIDENCE = 40   # Tesseract 0-100 confidence; isse kam wale words drop
# PSM 6 = "assume a single uniform block of text". Default automatic layout
# (PSM 3) galat se sparse/isolated columns (jaise "Yes"/"No" ke pair jo bahut
# space se ghire hote hain) ko non-text samajh kar poora drop kar deta hai —
# forms ke liye PSM 6 zyada complete aur reliable nikla.
TESSERACT_CONFIG = "--psm 6"

_TESS_ENV = "FORMFORGE_TESSERACT_CMD"
_OCR_DISABLED_ENV = "FORMFORGE_OCR_DISABLED"
_TESS_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]

_TRY_AI_INSTEAD = (
    "try the 'AI Parse (Gemini)' button above instead, it reads the page image "
    "directly and doesn't need OCR."
)


class OcrUnavailable(RuntimeError):
    """Page ka text nikaalne layak nahi hai — Tesseract missing hai, ya
    explicitly disabled kiya gaya hai (FORMFORGE_OCR_DISABLED)."""


def page_is_sparse(char_count: int, page: Any) -> bool:
    ink = len(page.curves) + len(page.rects) + len(page.lines) + (50 if page.images else 0)
    return char_count < SPARSE_CHAR_MAX and ink > SPARSE_INK_MIN


def ocr_disabled() -> bool:
    return os.environ.get(_OCR_DISABLED_ENV, "").strip().lower() in ("1", "true", "yes")


def _find_tesseract_cmd() -> str:
    env = os.environ.get(_TESS_ENV)
    if env and os.path.exists(env):
        return env
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    for candidate in _TESS_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    raise OcrUnavailable(
        f"This PDF's text layer is too sparse to read directly (likely a scan or a "
        f"flattened print) and the free local OCR engine (Tesseract) isn't installed "
        f"on this server — {_TRY_AI_INSTEAD} "
        "(Running your own server? Install Tesseract with: "
        "`winget install --id UB-Mannheim.TesseractOCR -e` "
        "(or from https://github.com/UB-Mannheim/tesseract/wiki), then try again — "
        f"for a custom install path, set the {_TESS_ENV} env var.)"
    )


def ocr_words(page: Any, resolution: int = OCR_RESOLUTION) -> List["Word"]:
    """Sparse-text page ko image bana kar Tesseract se words+bbox nikalta hai
    aur unhe PDF point-space mein `extract.Word` jaisa list return karta hai."""
    if ocr_disabled():
        # Tesseract yahan installed ho sakta hai (Docker image mein bundled),
        # par is host pe itna CPU-constrained ho sakta hai ki OCR minutes le le
        # aur platform ka apna request-timeout hit kar de (dekha gaya: Render
        # free tier ke 0.1 CPU pe ek scanned page ~1.5-2 min leta hai). Isse
        # explicitly band karke turant AI Parse ki taraf point karna behtar hai
        # is se ki ek slow, kabhi-kabhi-fail-hone-wali OCR attempt karna.
        raise OcrUnavailable(
            f"Local OCR is disabled on this server ({_OCR_DISABLED_ENV}=true) — {_TRY_AI_INSTEAD}"
        )
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise OcrUnavailable(
            "`pytesseract` package is not installed. Run `pip install pytesseract`."
        ) from exc

    from .extract import Word  # local import — extract.py isi module ko import karta hai

    pytesseract.pytesseract.tesseract_cmd = _find_tesseract_cmd()

    img = page.to_image(resolution=resolution)
    # Grayscale se recognize ~13% tez hota hai (benchmarked, no accuracy loss) —
    # Tesseract ko RGB->gray khud karna hi padta tha, PIL mein pehle hi kar do.
    pil_img = img.original.convert("L")
    scale = 72.0 / resolution  # image pixels -> PDF points (PDF = 72pt/inch)

    data = pytesseract.image_to_data(pil_img, config=TESSERACT_CONFIG, output_type=Output.DICT)
    words: List[Word] = []
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if conf < MIN_CONFIDENCE:
            continue
        x, y, w, h = (data["left"][i], data["top"][i], data["width"][i], data["height"][i])
        words.append(Word(
            text=text,
            x0=x * scale, x1=(x + w) * scale,
            top=y * scale, bottom=(y + h) * scale,
            size=h * scale,
            fontname="ocr",
        ))
    return words
