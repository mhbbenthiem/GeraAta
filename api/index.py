from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse, FileResponse
from pathlib import Path
import tempfile, traceback
from gerar_ata_core import (
    load_participantes_from_xlsx,
    get_df_for_filters,
    compose_text_core,
    create_pdf,
    core_self_check,
    supabase_ping,
    get_global_options,
    get_dependent_options,
    get_counts_summary,
)

app = FastAPI()

# --- util / erros
@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print("SERVERLESS ERROR:", tb)
    return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
# --- Home -> estático
@app.get("/")
def root():
    return {"ok": True, "routes": ["/health", "/options", "/participants", "/list_queue", "/compose_text", "/queue_ata", "/finalize_and_send"]}
# --- HEALTH (formato que seu JS espera)
@app.get("/health")  
def health():
    root = Path(__file__).resolve().parents[1]  
    ok_overall, details = core_self_check(root)
    sb_ok, sb_info = supabase_ping()
    payload = {
        "success": ok_overall,
        "status": "ok" if ok_overall else "fail",
        "counts": get_counts_summary(),
        "supabase_ok": sb_ok,
        "supabase_info": sb_info,
    }
    return JSONResponse(payload, status_code=200 if ok_overall else 500)

@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print("SERVERLESS ERROR:", tb)
    return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

# --- OPTIONS (globais e dependentes) — usado pelos selects do frontend
@app.get("/options")
def options(ano: str | None = None, turno: str | None = None):
    try:
        if ano or turno:
            data = get_dependent_options(ano, turno)
        else:
            data = get_global_options()
        return JSONResponse({"success": True, **data})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# --- PARTICIPANTES (já existia)
@app.get("/participants")
def participants(force: bool = False):
    return JSONResponse({"success": True, "participants": load_participantes_from_xlsx(force)})

# --- COMPOSE TEXT (já existia)
@app.post("/compose_text")
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

# --- GENERATE PDF (se quiser usar fora da fila)
@app.post("/generate_pdf")
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

# --- STUBS de FILA (para não quebrar a UI; em Vercel não há persistência)
@app.get("/list_queue")
def list_queue():
    return JSONResponse({"success": True, "queue": []})

@app.post("/reset_queue")
def reset_queue():
    return JSONResponse({"success": True})

@app.post("/queue_ata")
async def queue_ata(request: Request):
    # Aceita e responde sucesso para manter o fluxo da UI
    _ = await request.form()  # consumimos o body para não dar erro
    return JSONResponse({"success": True, "queued": 1, "message": "Fila desativada em serverless; use 'Pré-visualizar' e 'Gerar PDF'."})

@app.post("/finalize_and_send")
async def finalize_and_send(request: Request):
    _ = await request.form()
    return JSONResponse({"success": True, "message": "Envio/fila desativados no serverless. Gere e baixe o PDF pelo botão."})
