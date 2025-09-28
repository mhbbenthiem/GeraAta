# api/index.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from pathlib import Path
import tempfile
from app import (
    load_participantes_from_xlsx,
    get_df_for_filters,
    compose_text_core,
    create_pdf,
)
import tempfile, os, traceback

app = FastAPI()
ROOT = Path(__file__).parent.parent
PUBLIC = ROOT / "public"

app = FastAPI()
ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"

# --- middlewares simples de erro/JSON
@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    # Mostra erro útil pro cliente e escreve log completo
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print("SERVERLESS ERROR:", tb)
    return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

@app.get("/api/health")
def health():
    ok, details = core_self_check(ROOT)
    status = 200 if ok else 500
    return JSONResponse({"ok": ok, "details": details}, status_code=status)

@app.get("/")
def home():
    html_path = PUBLIC / "HTML_ata.html"
    if not html_path.exists():
        return PlainTextResponse("HTML_ata.html não encontrado em /public", status_code=500)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

@app.get("/api/participants")
def participants(force: bool = False):
    return JSONResponse({"success": True, "participants": load_participantes_from_xlsx(force)})

@app.post("/api/compose_text")
async def compose_text(request: Request):
    p = await request.json()
    required = ["ano","turno","turma","trimestre","numero_ata","data_reuniao","horario_inicio","horario_fim","presidente","participantes"]
    faltando = [k for k in required if not str(p.get(k,"")).strip()]
    if faltando:
        return JSONResponse({"success": False, "error": f"Campos obrigatórios ausentes: {', '.join(faltando)}"}, status_code=400)

    df_filt, column_map, df_base_tri = get_df_for_filters(p["ano"], p["turno"], p["turma"], p["trimestre"])
    if df_filt.empty:
        return JSONResponse({"success": False, "error": "Nenhum dado encontrado para os filtros."}, status_code=404)

    texto = compose_text_core(
        df_filt=df_filt, df_base_tri=df_base_tri, column_map=column_map,
        numero_ata=p["numero_ata"], data_reuniao=p["data_reuniao"],
        horario_inicio=p["horario_inicio"], horario_fim=p["horario_fim"],
        presidente=p["presidente"], participantes=p["participantes"],
        ano=p["ano"], turma=p["turma"], turno=p["turno"], trimestre=p["trimestre"],
    )
    return JSONResponse({"success": True, "texto": texto})

@app.post("/api/generate_pdf")
async def generate_pdf(request: Request):
    p = await request.json()
    required = ["ano","turno","turma","trimestre","numero_ata","data_reuniao","horario_inicio","horario_fim","presidente","participantes"]
    faltando = [k for k in required if not str(p.get(k,"")).strip()]
    if faltando:
        return JSONResponse({"success": False, "error": f"Campos obrigatórios ausentes: {', '.join(faltando)}"}, status_code=400)

    df_filt, column_map, df_base_tri = get_df_for_filters(p["ano"], p["turno"], p["turma"], p["trimestre"])
    if df_filt.empty:
        return JSONResponse({"success": False, "error": "Nenhum dado encontrado para os filtros."}, status_code=404)

    pdf_buffer = create_pdf(
        data=df_filt,
        numero_ata=p["numero_ata"], data_reuniao=p["data_reuniao"],
        horario_inicio=p["horario_inicio"], horario_fim=p["horario_fim"],
        presidente=p["presidente"], participantes=p["participantes"],
        ano=p["ano"], turma=p["turma"], turno=p["turno"], trimestre=p["trimestre"],
        override_text=p.get("texto_editado"),
        df_base_tri=df_base_tri, column_map=column_map,
    )
    tmp_pdf = Path(tempfile.gettempdir()) / f"ATA_{p['numero_ata']}.pdf"
    with open(tmp_pdf, "wb") as f:
        f.write(pdf_buffer.read())
    return FileResponse(str(tmp_pdf), media_type="application/pdf", filename=tmp_pdf.name)


@app.get("/")
def home():
    html = (PUBLIC / "HTML_ata.html").read_text(encoding="utf-8")
    return HTMLResponse(html)

@app.get("/api/participants")
def participants(force: bool = False):
    return JSONResponse({"success": True, "participants": load_participantes_from_xlsx(force)})

@app.post("/api/compose_text")
async def compose_text(request: Request):
    payload = await request.json()
    # campos obrigatórios
    required = ["ano","turno","turma","trimestre","numero_ata","data_reuniao","horario_inicio","horario_fim","presidente","participantes"]
    if any(not str(payload.get(k,"")).strip() for k in required):
        raise HTTPException(400, "Preencha todos os campos e filtros.")

    df_filt, column_map, df_base_tri = get_df_for_filters(
        payload["ano"], payload["turno"], payload["turma"], payload["trimestre"]
    )
    if df_filt.empty:
        raise HTTPException(404, "Nenhum dado encontrado para os filtros.")

    texto = compose_text_core(
        df_filt=df_filt,
        df_base_tri=df_base_tri,
        column_map=column_map,
        numero_ata=payload["numero_ata"],
        data_reuniao=payload["data_reuniao"],
        horario_inicio=payload["horario_inicio"],
        horario_fim=payload["horario_fim"],
        presidente=payload["presidente"],
        participantes=payload["participantes"],
        ano=payload["ano"],
        turma=payload["turma"],
        turno=payload["turno"],
        trimestre=payload["trimestre"],
    )
    return JSONResponse({"success": True, "texto": texto})

@app.post("/api/generate_pdf")
async def generate_pdf(request: Request):
    payload = await request.json()
    required = ["ano","turno","turma","trimestre","numero_ata","data_reuniao","horario_inicio","horario_fim","presidente","participantes"]
    if any(not str(payload.get(k,"")).strip() for k in required):
        raise HTTPException(400, "Preencha todos os campos e filtros.")

    df_filt, column_map, df_base_tri = get_df_for_filters(
        payload["ano"], payload["turno"], payload["turma"], payload["trimestre"]
    )
    if df_filt.empty:
        raise HTTPException(404, "Nenhum dado encontrado para os filtros.")

    pdf_buffer = create_pdf(
        data=df_filt,
        numero_ata=payload["numero_ata"],
        data_reuniao=payload["data_reuniao"],
        horario_inicio=payload["horario_inicio"],
        horario_fim=payload["horario_fim"],
        presidente=payload["presidente"],
        participantes=payload["participantes"],
        ano=payload["ano"],
        turma=payload["turma"],
        turno=payload["turno"],
        trimestre=payload["trimestre"],
        override_text=payload.get("texto_editado"),
        df_base_tri=df_base_tri,
        column_map=column_map,
    )

    tmp_pdf = Path(tempfile.gettempdir()) / f"ATA_{payload['numero_ata']}.pdf"
    with open(tmp_pdf, "wb") as f:
        f.write(pdf_buffer.read())
    return FileResponse(str(tmp_pdf), media_type="application/pdf", filename=tmp_pdf.name)
