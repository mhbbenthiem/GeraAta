from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from pathlib import Path
import tempfile, io, os
from gerar_ata_core import (
    load_participantes_from_xlsx,
    create_pdf,
    get_df_for_filters,
)

app = FastAPI()

ROOT = Path(__file__).parent.parent
PUBLIC = ROOT / "public"

@app.get("/")
def index():
    html = (PUBLIC / "HTML_ata.html").read_text(encoding="utf-8")
    return HTMLResponse(html)

@app.get("/api/participants")
def participants(force: bool = False):
    return JSONResponse({"success": True, "participants": load_participantes_from_xlsx(force)})

@app.post("/api/compose_text")
async def compose_text(request: Request):
    payload = await request.json()
    # mesmos campos do seu endpoint atual
    ano = payload.get("ano"); turno = payload.get("turno"); turma = payload.get("turma"); trimestre = payload.get("trimestre")
    numero_ata = payload.get("numero_ata"); data_reuniao = payload.get("data_reuniao")
    horario_inicio = payload.get("horario_inicio"); horario_fim = payload.get("horario_fim")
    presidente = payload.get("presidente"); participantes = payload.get("participantes")

    df_filt, column_map, df_base_tri = get_df_for_filters(ano, turno, turma, trimestre)
    if df_filt.empty:
        raise HTTPException(404, "Nenhum dado encontrado para os filtros.")

    # Reuse sua própria montagem de texto chamando o mesmo bloco que o PDF usa
    pdf_buf = create_pdf(
        df_filt,
        numero_ata, data_reuniao, horario_inicio, horario_fim,
        presidente, participantes, ano, turma, turno, trimestre,
        override_text=None
    )
    # Extraia só o texto também, se precisar (ou tenha uma função compose_text pura)
    return JSONResponse({"success": True, "texto": "(gere aqui o mesmo texto que vai no PDF)"})

@app.post("/api/generate_pdf")
async def generate_pdf(request: Request):
    payload = await request.json()
    ano = payload["ano"]; turno = payload["turno"]; turma = payload["turma"]; trimestre = payload["trimestre"]
    numero_ata = payload["numero_ata"]; data_reuniao = payload["data_reuniao"]
    horario_inicio = payload["horario_inicio"]; horario_fim = payload["horario_fim"]
    presidente = payload["presidente"]; participantes = payload["participantes"]
    texto_editado = payload.get("texto_editado")

    df_filt, column_map, df_base_tri = get_df_for_filters(ano, turno, turma, trimestre)
    if df_filt.empty:
        raise HTTPException(404, "Nenhum dado encontrado para os filtros.")

    pdf_buffer = create_pdf(
        df_filt,
        numero_ata, data_reuniao, horario_inicio, horario_fim,
        presidente, participantes, ano, turma, turno, trimestre,
        override_text=texto_editado
    )

    tmp_pdf = Path(tempfile.gettempdir()) / f"ATA_{numero_ata}.pdf"
    with open(tmp_pdf, "wb") as f:
        f.write(pdf_buffer.read())

    return FileResponse(
        path=str(tmp_pdf),
        media_type="application/pdf",
        filename=f"ATA_{numero_ata}.pdf"
    )
