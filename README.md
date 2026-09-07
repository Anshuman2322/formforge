# FormForge — PDF se editable form template (prototype)

Ek PDF form upload karo → system uska structure khud samajhta hai (heading, section,
checkbox group, Yes/No question, table, signature, date, blank field) → ek JSON schema
banata hai → aur wo schema ek drag-drop builder mein editable form ban ke khulta hai.

KareXpert ke template-builder jaisa workflow, par input ek scan/print form hai — manual
drag-drop se poora form banane ki zaroorat nahi.

---

## Chalane ka tarika

```bash
pip install -r requirements-dev.txt               # base deps + reportlab (sirf make_samples.py ke liye)
winget install --id UB-Mannheim.TesseractOCR -e   # free OCR engine (scanned/flattened PDFs ke liye)
python samples/make_samples.py                    # do test PDFs banata hai
uvicorn backend.app:app --reload --port 8000
# browser: http://127.0.0.1:8000
```

**Production deploy**: `--reload` sirf ek dev file-watcher hai, production mein mat use
karo. `pip install -r requirements.txt` (reportlab ke bina — wo sirf sample-generation
ke liye hai) aur `uvicorn backend.app:app --host 0.0.0.0 --port $PORT` chalao. `.env`
file ki jagah deployment platform ke apne secret/env-var store se `GEMINI_API_KEY`
set karo (aur zaroorat ho to `FORMFORGE_CORS_ORIGINS` bhi — neeche dekhein).

Scanned ya text-outlined-as-curves PDFs (jaise print/flatten se aayi hospital
forms) ke liye Tesseract OCR engine chahiye (upar wala `winget` command,
ya https://github.com/UB-Mannheim/tesseract/wiki se manually) — 100% free,
offline, koi API key ya billing nahi. Digital-text PDFs bina Tesseract ke bhi
chal jaate hain.

**Har upload sirf local pipeline se guzarta hai — koi external API kabhi apne
aap nahi chalti.** Ek opt-in **AI Parse** button hai (Google Gemini, free
tier) jo *sirf* explicit click par chalta hai:

```bash
cp .env.example .env       # phir .env mein GEMINI_API_KEY=... daal do
```

Key https://aistudio.google.com/apikey se free milti hai. Key na ho (ya
`FORMFORGE_AI_DISABLED=true` set ho) to button builder mein clearly
**disabled** dikhta hai apne reason ke saath — app crash nahi karta, bas
local-only reh jaata hai. Free-tier rate-limit se bachne ke liye requests
5-second gap se queue hoti hain aur 429 par exponential backoff retry karti
hain — bina iske multi-page PDF ka AI Parse fail ho jaata.

**Privacy**: AI Parse ki page-image Google ko jaati hai; free tier ka content
Google product-improvement ke liye use ho sakta hai (builder mein button ke
paas isi ki chhoti line hai).

**Other env vars** (sab optional, `.env.example` mein documented):
`FORMFORGE_CORS_ORIGINS` (comma-separated allowed origins, default `*`),
`FORMFORGE_MAX_UPLOAD_MB` (default 20), `FORMFORGE_GEMINI_MODEL`,
`FORMFORGE_TESSERACT_CMD` (custom Tesseract install path).

---

## Architecture

```
PDF ──► extract.py ──► classify.py ──► memory.py ──► schema.py ──► builder UI ──► JSON
        (geometry)     (semantics)     (learned      (data model)  (human fix)    (export)
           │                            corrections)
           ├── ocr_fallback.py   (page ka text layer sparse ho to free/local Tesseract OCR)
           └── gemini_parse.py   (opt-in, "AI Parse" click par — Gemini vision se
                                   poora page seedha, extract.py/classify.py bypass)
```

### 1. `backend/extract.py` — layout extraction
pdfplumber se words + unke exact coordinates, stroked lines, rects aur ruled tables.
Isse banta hai:
- **text lines** — words visually grouped by y-coordinate
- **checkbox squares** — 4–18pt ke square rects
- **rules** — patli horizontal lines (= fill-in-the-blank)
- **tables** — ruled grids (in ke andar ki lines stream se hata di jaati hain)

Koi interpretation nahi — sirf geometry.

Kuch PDFs mein page ka text layer extract nahi hota — ya to scan hai, ya kisi
print/flatten step ne asli font glyphs ko vector curves mein "outline" kar diya
hai (dono cases mein `pdfplumber` ko koi real text nahi milta). Aisa page
detect hote hi (bahut kam extracted characters + bahut zyada curves/rects/lines)
`backend/ocr_fallback.py` us page ko image bana kar **Tesseract** (free,
open-source, fully offline OCR engine) se words + unke coordinates nikalta hai
— aur wahi words upar wali `_group_lines` / checkbox-rule-attach pipeline se
guzarte hain, taaki neeche wale saare detectors (checkbox, Yes/No, blanks,
heading...) unpe bhi waise hi chalein jaise digital-text PDFs par chalte hain.
Koi API key ya paid service nahi lagta — sirf Tesseract engine system par
installed hona chahiye. OCR se aaye components ka confidence hamesha capped
(≤ 0.65) rehta hai, taaki builder UI mein wo amber "check this" badge ke saath
review ke liye highlight hon (OCR geometry-rules jitna precise nahi hota).
Tesseract missing ho to chup-chaap galat/khali template nahi banta — clear
error milta hai install command ke saath.

### 2. `backend/classify.py` — structure detection (dimaag)
Har visual line pe detectors priority order mein chalte hain:

| # | Detector | Kya banta hai |
|---|----------|---------------|
| 1 | checkbox squares line pe | `checkboxgroup` / `checkbox` — square ke right ka text = option label |
| 2 | line ke end mein `Yes No`, `Not sure Yes No`, `Yes/No` | `radiogroup` |
| 3 | underscores ya ruled underline | `textbox` / `date` / `number` / `textarea` — blank se pehle ka text = label |
| 4 | bada ya bold ya ALL-CAPS text | `heading` / `subheading` |
| 5 | `Label:` colon pe khatam | input field |
| 6 | `...?` question | input field |
| 7 | baaki | `paragraph` (consecutive lines merge ho jaati hain) |

Uske upar document-level rules:
- **Wrapped options merge** — "Local" agli line pe hai to bhi usi checkbox group mein jaata hai
- **Heading → label promotion** — "TYPE OF ANAESTHESIA RECOMMENDED" + neeche checkbox row = ek hi labelled group
- **Sub-question header** — "Do you have any of the following conditions?" ke neeche indented Yes/No items hain to wo line input nahi, section header banti hai
- **Type inference** — label keywords se `date` / `time` / `number` / `signature` / `fileupload` / `textarea`
- **Side-by-side widths** — ek line pe 3 blanks = teenon fields 4/12 width ke

Har component pe **confidence score** hota hai. `< 0.8` wale builder mein amber
"check this" badge ke saath dikhte hain — reviewer ko pata rehta hai kahan dekhna hai.

### 3. `backend/memory.py` — correction memory (ML training NAHI)
Ek badhta hua pattern store, ek hi example se kaam karta hai. Jab user
builder mein template final **Save** karta hai, frontend upload-time ka
"as-parsed" snapshot (`original`) aur ab ke corrected components dono bhejta
hai; backend dono compare karke teen tarah ki cheezein seekhta hai:

- **line_map** — poori line ka normalized text → final {type, label, options}.
  Sabse bada win, kyunki hospital forms mein ek hi line dozens forms mein
  repeat hoti hai.
- **keyword_map** — ek significant keyword → type (jaise "creatinine" → number).
- **patterns** — naya structural pattern (jaise "Yes No" ka rule already
  hardcoded hai classify.py mein — koi doosri bhasha/format ka aisa hi
  suffix pattern yahan seekha jaata hai).

Apply hamesha rules ke **baad** hoti hai (`/api/parse` mein `classify.pdf_to_template`
ke turant baad `memory.apply_memory` chalta hai) — koi bhi entry sirf 2+ baar
dekhe jaane par auto-apply hoti hai, aur confidence hamesha < 0.8 rakhi
jaati hai taaki reviewer ko "check this" badge dikhta rahe. 100% local/free,
koi API nahi.

### 4. `frontend/index.html` — builder
Zero dependencies, single file. Left palette (add field), center canvas
(drag to reorder, click to select, duplicate/delete), right inspector
(type, label, key, width, required, options, table columns). Preview mode,
JSON view, form.io export, save.

---

## API

| Method | Route | Kaam |
|--------|-------|------|
| POST | `/api/parse?engine=rules\|ai` | PDF upload → template JSON. `rules` (default, free, local) ya `ai` (opt-in, Gemini vision, sirf explicit request par) |
| POST | `/api/templates` | template save — `original` bhejo to correction memory update hoti hai |
| GET | `/api/templates` | saved list |
| GET | `/api/templates/{id}` | ek template |
| POST | `/api/export/formio` | neutral schema → form.io/KareXpert style JSON |

---

## Schema

```json
{
  "title": "Informed Consent For Anaesthesia",
  "schemaVersion": "1.0",
  "components": [
    {
      "id": "c_a1b2c3d4",
      "type": "checkboxgroup",
      "label": "TYPE OF ANAESTHESIA RECOMMENDED",
      "key": "type_of_anaesthesia_recommended",
      "options": [{"id": "general_anaesthesia", "label": "General Anaesthesia"}],
      "optionLayout": "horizontal",
      "required": false,
      "width": 12,
      "order": 3,
      "confidence": 0.85,
      "detectedBy": "rule",
      "source": {"page": 1, "bbox": [45, 118, 520, 130], "text": "..."}
    }
  ]
}
```

Types: `heading, subheading, paragraph, divider, textbox, textarea, number, date,
time, dropdown, radiogroup, checkboxgroup, checkbox, table, signature, fileupload, fieldset`

`schema.py` ka `to_formio()` isko form.io-style components array mein badalta hai.
KareXpert ka exact export JSON mil jaye to sirf `_KX_TYPE_MAP` aur `conv()` tweak
karne honge — baaki pipeline same.

---

## Abhi kya nahi hai (agla phase)

- **OCR se aaye components ka confidence hamesha capped hota hai (≤ 0.65)** — Tesseract
  ka text geometry-rules jitna precise nahi, isliye builder mein har OCR field "check
  this" badge ke saath aata hai.
- **Pure raster scans (koi vector graphics nahi, sirf ek poori image)** abhi OCR se sirf
  text nikaal paate hain — checkbox squares aur ruled underlines abhi bhi PDF ke vector
  rects/lines se detect hote hain, isliye agar scan mein wo bilkul nahi hain (purely
  flat image) to un fields ka blank/checkbox-ness miss ho sakta hai. Is repo mein test
  kiya gaya real-world case (print/flatten se aayi hospital form) mein vector shapes bach
  jaate hain, isliye ye kaam karta hai — lekin agla phase: image-based shape detection.
- **Nested/fieldset layout** — schema mein support hai, detector abhi flat list banata hai.
- **Conditional logic** — "If YES, then…" wali lines abhi paragraph banti hain; inko show/hide rule banaya ja sakta hai.
- **Side-by-side PDF preview** — builder mein original page image dikhe aur component pe click karne se wo region highlight ho (`source.bbox` already store ho raha hai, bas render karna hai).
- **Auth / multi-tenant storage** — abhi templates ek shared local `storage/` folder mein
  JSON files ki tarah save hote hain, koi login/user-separation nahi hai. Har request
  koi bhi template save/read/list kar sakta hai — single-user/internal-tool use ke liye
  theek hai, multi-tenant deploy se pehle auth chahiye.

### Roadmap (agle steps, abhi nahi bane)
1. ~~Correction memory~~ ✅ ban gaya (`backend/memory.py`)
2. Escalation UI — weak local-parse result par "AI Parse try karein?" banner (button already
   hamesha available hai, sirf auto-suggest banner skip kiya gaya abhi)
3. ~~Gemini vision parse~~ ✅ ban gaya (`backend/gemini_parse.py`) — few-shot examples
   correction-memory se prompt mein jaate hain
4. Form fingerprint matching — same/revised form dobara aaye to purana template suggest karna
5. Dataset collection (original + local + AI + corrected, sab store) + admin memory-review screen
6. ~~Security hardening (template id validation, CORS restrict, upload size limit)~~ ✅ ban gaya —
   `id` ab `[A-Za-z0-9_-]` tak restricted hai (path-traversal fix), CORS `FORMFORGE_CORS_ORIGINS`
   se restrict ho sakta hai (default abhi bhi `*`, local dev ke liye), upload `FORMFORGE_MAX_UPLOAD_MB`
   (default 20) se capped hai. Auth abhi bhi nahi hai (upar dekhein).
