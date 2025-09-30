# /api/index.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
import os, sys, traceback
from pathlib import Path

app = FastAPI(title="GeraAta API")

# --- diagnostico de import ---
IMPORT_ERROR = None
CORE = {}

def _try_import_core():
    global IMPORT_ERROR, CORE
    if CORE:
        return
    try:
        # garante que o diretório desta função está no sys.path
        here = Path(__file__).resolve().parent
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))

        # importa utilitários locais (mesma pasta /api)
        import gerar_ata_core  # noqa: F401
        CORE = {
            "module": gerar_ata_core,
            "supabase_ping": getattr(gerar_ata_core, "supabase_ping", None),
            "options_global": getattr(gerar_ata_core, "options_global", None),
            "options_filtered": getattr(gerar_ata_core, "options_filtered", None),
            "get_participants": getattr(gerar_ata_core, "get_participants", None),
            "list_queue": getattr(gerar_ata_core, "list_queue", None),
            "reset_queue": getattr(gerar_ata_core, "reset_queue", None),
            "compose_text": getattr(gerar_ata_core, "compose_text", None),
            "enqueue_ata": getattr(gerar_ata_core, "enqueue_ata", None),
            "finalize_and_send": getattr(gerar_ata_core, "finalize_and_send", None),
        }
    except Exception as e:
        IMPORT_ERROR = f"{e.__class__.__name__}: {e}\n" + traceback.format_exc()

_try_import_core()

# --- rotas base / diagnostico ---
@app.get("/")
def root():
    return {
        "ok": True,
        "hint": "use /health, /options, /participants, /list_queue, /compose_text, /queue_ata, /finalize_and_send",
        "import_error": IMPORT_ERROR is not None
    }

@app.get("/docs_redirect")
def docs_redirect():
    return RedirectResponse(url="/api/index/docs")

@app.get("/debug_imports")
def debug_imports():
    here = str(Path(__file__).resolve().parent)
    return {
        "cwd": os.getcwd(),
        "here": here,
        "sys_path_has_here": here in sys.path,
        "import_error": IMPORT_ERROR,
        "core_keys": sorted(list(CORE.keys())),
        "core_missing": [k for k,v in CORE.items() if k != "module" and v is None],
        "env_flags": {
            "SUPABASE_URL_set": bool(os.getenv("SUPABASE_URL")),
            "SUPABASE_KEY_set": bool(os.getenv("SUPABASE_KEY")),
        }
    }

# --- rotas reais usadas pelo front ---
@app.get("/health")
def health():
    if IMPORT_ERROR: 
        return JSONResponse({"success": False, "error": f"import failed: {IMPORT_ERROR}"}, status_code=500)
    ping = CORE["supabase_ping"]
    if not ping:
        return {"success": True, "status": "ok", "note": "supabase_ping ausente (usando stub?)"}
    ok, info = ping()
    return {"success": True, "status": "ok" if ok else "degraded", "env_configured": info, "counts": getattr(CORE["module"], "COUNTS", {})}

@app.get("/options")
def options(ano: str | None = None, turno: str | None = None):
    if IMPORT_ERROR:
        return JSONResponse({"success": False, "error": f"import failed: {IMPORT_ERROR}"}, status_code=500)
    if ano or turno:
        fn = CORE["options_filtered"] or CORE["options_global"]
        data = fn(ano=ano, turno=turno) if fn else {}
    else:
        fn = CORE["options_global"]
        data = fn() if fn else {}
    return {"success": True, **(data or {})}

@app.get("/participants")
def participants(force: int = 0):
    if IMPORT_ERROR:
        return JSONResponse({"success": False, "error": f"import failed: {IMPORT_ERROR}"}, status_code=500)
    fn = CORE["get_participants"]
    if not fn:
        return {"success": False, "error": "get_participants não encontrado em gerar_ata_core.py"}
    lst = fn(force=bool(force))
    return {"success": True, "participants": lst}

@app.get("/list_queue")
def list_queue():
    if IMPORT_ERROR:
        return JSONResponse({"success": False, "error": f"import failed: {IMPORT_ERROR}"}, status_code=500)
    fn = CORE["list_queue"]
    q = fn() if fn else []
    return {"success": True, "queue": q}

@app.post("/reset_queue")
def reset_queue():
    if IMPORT_ERROR:
        return JSONResponse({"success": False, "error": f"import failed: {IMPORT_ERROR}"}, status_code=500)
    fn = CORE["reset_queue"]
    ok = fn() if fn else False
    return {"success": bool(ok)}

@app.post("/compose_text")
async def compose_text(req: Request):
    if IMPORT_ERROR:
        return JSONResponse({"success": False, "error": f"import failed: {IMPORT_ERROR}"}, status_code=500)
    payload = await req.json()
    fn = CORE["compose_text"]
    if not fn:
        return {"success": False, "error": "compose_text não encontrado"}
    txt = fn(**payload)
    return {"success": True, "texto": txt}

@app.post("/queue_ata")
async def queue_ata(req: Request):
    if IMPORT_ERROR:
        return JSONResponse({"success": False, "error": f"import failed: {IMPORT_ERROR}"}, status_code=500)
    form = await req.form()
    fn = CORE["enqueue_ata"]
    ok = fn(form) if fn else False
    return {"success": bool(ok)}

@app.post("/finalize_and_send")
async def finalize_and_send(req: Request):
    if IMPORT_ERROR:
        return JSONResponse({"success": False, "error": f"import failed: {IMPORT_ERROR}"}, status_code=500)
    form = await req.form()
    fn = CORE["finalize_and_send"]
    res = fn(email=form.get("email")) if fn else {"ok": False}
    if isinstance(res, dict):
        return {"success": res.get("ok", False), **res}
    return {"success": bool(res)}
