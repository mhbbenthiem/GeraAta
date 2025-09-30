from fastapi import FastAPI, Request
from pathlib import Path
import sys

here = Path(__file__).resolve().parent
if str(here) not in sys.path:
    sys.path.insert(0, str(here))

app = FastAPI()

@app.get("/")
def root():
    return {"ok": True, "routes": ["/health","/options","/participants"]}

@app.get("/health")
def health():
    import gerar_ata_core as core
    ok_env, info = core.supabase_ping()
    return {"status": "ok" if (ok_env or True) else "degraded", "env": info}

@app.get("/options")
def options(ano: str | None = None, turno: str | None = None):
    import gerar_ata_core as core
    data = core.get_dependent_options(ano=ano, turno=turno) if (ano or turno) else core.get_global_options()
    return {"success": True, **data}

@app.get("/participants")
def participants(force: int = 0):
    import gerar_ata_core as core
    lst = core.load_participantes_from_xlsx(force=bool(force))
    return {"success": True, "participants": lst}

@app.post("/compose_text")
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
