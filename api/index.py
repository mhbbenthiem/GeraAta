# /api/index.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pathlib import Path
import sys, os
import pandas as pd

# garante import local
here = Path(__file__).resolve().parent
if str(here) not in sys.path:
    sys.path.insert(0, str(here))

import gerar_ata_core as core  # usa os nomes reais do arquivo

app = FastAPI(title="GeraAta API (wired)")
@app.get("/")
def root():
    return {"ok": True, "routes": ["/health"]}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/options")
def options(ano: str | None = None, turno: str | None = None):
    if ano or turno:
        data = core.get_dependent_options(ano=ano, turno=turno)
    else:
        data = core.get_global_options()
    return {"success": True, **data}

@app.get("/participants")
def participants(force: int = 0):
    lst = core.load_participantes_from_xlsx(force=bool(force))
    return {"success": True, "participants": lst}

@app.get("/list_queue")
def list_queue():
    # placeholder: ainda não há fila no core
    return {"success": True, "queue": []}

@app.post("/reset_queue")
def reset_queue():
    # placeholder
    return {"success": True}

@app.post("/compose_text")
async def compose_text(req: Request):
    payload = await req.json()
    # monta os dataframes necessários
    df_filt, colmap, df_base_tri = core.get_df_for_filters(
        ano=payload.get("ano"), turno=payload.get("turno"),
        turma=payload.get("turma"), trimestre=payload.get("trimestre")
    )
    # se quiser usar texto editado, trate aqui
    txt = core.compose_text_core(
        df_filt=df_filt,
        df_base_tri=df_base_tri,
        column_map=colmap,
        numero_ata=payload.get("numero_ata"),
        data_reuniao=payload.get("data_reuniao"),
        horario_inicio=payload.get("horario_inicio"),
        horario_fim=payload.get("horario_fim"),
        presidente=payload.get("presidente"),
        participantes=payload.get("participantes"),
        ano=payload.get("ano"),
        turma=payload.get("turma"),
        turno=payload.get("turno"),
        trimestre=payload.get("trimestre"),
    )
    return {"success": True, "texto": txt}

@app.post("/queue_ata")
async def queue_ata(req: Request):
    # placeholder: sem fila persistida por enquanto
    return {"success": True}

@app.post("/finalize_and_send")
async def finalize_and_send(req: Request):
    # placeholder: sem e-mail/zip no core atual
    return {"success": True, "ok": True, "message": "Processo finalizado (stub)."}
