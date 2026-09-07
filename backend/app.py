"""FormForge API — PDF upload se editable form template tak."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import gemini_parse, memory
from .classify import pdf_to_template
from .schema import to_formio

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"
STORAGE = BASE / "storage"
STORAGE.mkdir(exist_ok=True)

MAX_UPLOAD_MB = int(os.environ.get("FORMFORGE_MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024

TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _validate_template_id(tid: str) -> str:
    """`id` file-path banane mein seedha use hota hai — bina is check ke
    ek crafted id (jaise "../../secret") storage folder se bahar likh/padh
    sakta tha. Sirf safe filename characters allow karo."""
    if not TEMPLATE_ID_RE.match(tid or ""):
        raise HTTPException(400, "Invalid template id")
    return tid


def _load_dotenv(path: Path) -> None:
    """.env se KEY=VALUE lines load karo (agar env var already set nahi hai)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE / ".env")

app = FastAPI(title="FormForge", version="0.1.0")

# Default is wide-open ("*") for zero-config local dev. For a real deployment,
# set FORMFORGE_CORS_ORIGINS to a comma-separated allowlist (e.g. your frontend's
# domain) instead of leaving every origin allowed.
_cors_env = os.environ.get("FORMFORGE_CORS_ORIGINS", "*").strip()
CORS_ORIGINS = ["*"] if _cors_env in ("", "*") else [o.strip() for o in _cors_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"]
)


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "ai": gemini_parse.availability()}


@app.post("/api/parse")
async def parse_pdf(
    file: UploadFile = File(...),
    engine: str = Query(
        "rules",
        description="'rules' (default) = 100% local, free — geometry rules + Tesseract OCR "
                     "fallback + learned correction-memory. 'ai' = opt-in, only runs on explicit "
                     "user request — Gemini vision reads the whole page directly (free tier).",
    ),
):
    """Rules path hamesha default hai aur kabhi bhi apne aap AI nahi chalati.
    'ai' sirf tab chalta hai jab caller explicitly maange (frontend ka "AI
    Parse" button) — kabhi automatic nahi."""
    # .name se koi bhi directory component (jaise "../../evil.pdf") strip ho jaata
    # hai — filename seedha disk path banane mein use hota hai, isliye sanitize
    # zaroori hai warna crafted filename se temp-dir ke bahar likha ja sakta tha.
    safe_name = Path(file.filename or "upload.pdf").name
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are allowed")
    if engine not in ("rules", "ai"):
        raise HTTPException(400, "engine must be 'rules' or 'ai'")

    tmp_dir = Path(tempfile.mkdtemp())
    tmp = tmp_dir / safe_name
    try:
        size = 0
        with tmp.open("wb") as fh:
            while chunk := await file.read(UPLOAD_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB)")
                fh.write(chunk)

        t0 = time.time()
        memory_report = None
        try:
            title = tmp.stem.replace("_", " ").title()
            if engine == "ai":
                # Network calls + rate-limit sleeps hain — event loop block na ho.
                template = await run_in_threadpool(gemini_parse.gemini_parse_document, str(tmp), title)
            else:
                template = pdf_to_template(str(tmp), title=title)
                memory_report = memory.apply_memory(template)
        except Exception as exc:
            raise HTTPException(422, f"Could not parse PDF: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    data = template.to_dict()
    data["meta"].update({
        "parseSeconds": round(time.time() - t0, 2),
        "engine": engine,
        "memory": memory_report,
        "sourceFile": safe_name,
    })
    return JSONResponse(data)


@app.post("/api/templates")
async def save_template(payload: Dict[str, Any]):
    tid = _validate_template_id(str(payload.get("id"))) if payload.get("id") else f"tpl_{int(time.time())}"
    payload["id"] = tid
    payload["savedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")

    original = payload.pop("original", None)
    if original is not None:
        memory.learn_from_correction(
            original, payload.get("components", []),
            author=str(payload.get("author") or "local-user"),
        )

    (STORAGE / f"{tid}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return {"ok": True, "id": tid, "savedAt": payload["savedAt"], "path": f"storage/{tid}.json"}


@app.get("/api/templates")
def list_templates() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(STORAGE.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.name == memory.STORE_PATH.name:  # correction-memory store, not a template
            continue
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        out.append({
            "id": d.get("id", p.stem),
            "title": d.get("title", p.stem),
            "savedAt": d.get("savedAt", ""),
            "fields": len(d.get("components", [])),
        })
    return out


@app.get("/api/templates/{tid}")
def get_template(tid: str):
    tid = _validate_template_id(tid)
    p = STORAGE / f"{tid}.json"
    if not p.exists():
        raise HTTPException(404, "Template not found")
    return json.loads(p.read_text())


@app.post("/api/export/formio")
async def export_formio(payload: Dict[str, Any]):
    """Neutral schema -> form.io / KareXpert-style JSON."""
    from .schema import Column, Component, Option, Template

    comps = []
    for c in payload.get("components", []):
        comp = Component(
            type=c.get("type", "textbox"),
            label=c.get("label", ""),
            key=c.get("key", ""),
            required=bool(c.get("required")),
            width=int(c.get("width", 12)),
            optionLayout=c.get("optionLayout", "horizontal"),
            options=[Option(id=o.get("id", ""), label=o.get("label", ""))
                     for o in c.get("options", [])],
            columns=[Column(key=col.get("key", ""), label=col.get("label", ""),
                            type=col.get("type", "textbox"))
                     for col in c.get("columns", [])],
        )
        comps.append(comp)
    tpl = Template(title=payload.get("title", "Untitled"), components=comps)
    return to_formio(tpl)
