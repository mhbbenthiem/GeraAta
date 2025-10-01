# api/index.py — Render-ready
import os, sys
from pathlib import Path
from fastapi import FastAPI, APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware

# garante import local
here = Path(__file__).resolve().parent
if str(here) not in sys.path:
    sys.path.insert(0, str(here))

app = FastAPI(title="GeraAta API")

# 1) CORS: ajuste para o domínio REAL do seu front
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2) Prefixo configurável: no Render use /api/index para casar com o front
API_PREFIX = os.getenv("API_PREFIX", "/api/index")
api = APIRouter(prefix=API_PREFIX)

@api.get("/")
def root():
    return {"ok": True, "routes": [f"{API_PREFIX}/health", f"{API_PREFIX}/options", f"{API_PREFIX}/participants"]}

@api.get("/health")
def health():
    import gerar_ata_core as core
    ok_env, info = core.supabase_ping()
    counts = core.get_counts_summary()
    return {
        "success": True,
        "status": "ok",
        "env_configured": {"SUPABASE_URL_set": info["SUPABASE_URL_set"], "SUPABASE_KEY_set": info["SUPABASE_KEY_set"]},
        "counts": counts,
    }

@api.get("/options")
def options(ano: str | None = None, turno: str | None = None):
    import gerar_ata_core as core
    data = core.get_dependent_options(ano=ano, turno=turno) if (ano or turno) else core.get_global_options()
    return {"success": True, **data}

@api.get("/participants")
def participants(force: int = 0):
    import gerar_ata_core as core
    lst = core.load_participantes_from_xlsx(force=bool(force))
    return {"success": True, "participants": lst}

@api.post("/compose_text")
async def compose_text(req: Request):
    import gerar_ata_core as core
    payload = await req.json()
    df_filt, colmap, df_base_tri = core.get_df_for_filters(
        ano=payload.get("ano"), turno=payload.get("turno"),
        turma=payload.get("turma"), trimestre=payload.get("trimestre")
    )
    txt = core.compose_text_core(
        df_filt=df_filt, df_base_tri=df_base_tri, column_map=colmap,
        numero_ata=payload.get("numero_ata"), data_reuniao=payload.get("data_reuniao"),
        horario_inicio=payload.get("horario_inicio"), horario_fim=payload.get("horario_fim"),
        presidente=payload.get("presidente"), participantes=payload.get("participantes"),
        ano=payload.get("ano"), turma=payload.get("turma"),
        turno=payload.get("turno"), trimestre=payload.get("trimestre"),
    )
    return {"success": True, "texto": txt}

# Stubs para não quebrar os botões do front
@api.get("/list_queue")
def list_queue(): return {"success": True, "queue": []}

@api.post("/reset_queue")
def reset_queue(): return {"success": True}

@api.post("/queue_ata")
async def queue_ata(_: Request): return {"success": True}

@api.post("/finalize_and_send")
async def finalize_and_send(_: Request): return {"success": True, "message": "Processo finalizado (stub)"}

app.include_router(api)
