from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from pathlib import Path
import tempfile, traceback

from gerar_ata_core import (
    load_participantes_from_xlsx,
    get_df_for_filters,
    compose_text_core,
    create_pdf,
    core_self_check,
)

app = FastAPI()

@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print("SERVERLESS ERROR:", tb)
    return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

@app.get("/")
def home():
    return RedirectResponse("/HTML_ata.html", status_code=302)

@app.get("/api/health")
def health():
    root = Path(__file__).resolve().parents[1]
    ok, details = core_self_check(root)
    return JSONResponse({"ok": ok, "details": details}, status_code=200 if ok else 500)

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
