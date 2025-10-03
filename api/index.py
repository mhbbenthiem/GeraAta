# api/index.py — Render-ready
import os, sys, zipfile, smtplib, ssl, io
from pathlib import Path
from email.message import EmailMessage
from typing import List, Tuple
from fastapi import FastAPI, APIRouter, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# garante import local
here = Path(__file__).resolve().parent
if str(here) not in sys.path:
    sys.path.insert(0, str(here))

app = FastAPI(title="GeraAta API")

# --- Infra local para fila/arquivos -----------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", here))  # mesmo dir por padrão
OUT_DIR  = DATA_DIR / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)
ZIP_PATH = OUT_DIR / "atas.zip"

# Estrutura simples de fila na memória:
# Cada item: {"filename": str, "path": Path, "size": int}
QUEUE: list[dict] = []

def _safe_int(x, default=0):
    try: return int(x)
    except: return default

def _queue_snapshot() -> list[dict]:
    snap=[]
    for it in QUEUE:
        p = Path(it["path"])
        snap.append({
            "filename": it.get("filename") or p.name,
            "size": it.get("size") or (p.stat().st_size if p.exists() else 0),
        })
    return snap

def _send_email_with_attachment(
    subject: str,
    body: str,
    to_addrs: List[str],
    attach_path: Path,
    cc_addrs: List[str] | None = None,
) -> Tuple[bool, str]:
    host = os.getenv("SMTP_HOST")
    port = _safe_int(os.getenv("SMTP_PORT", 587), 587)
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    tls  = os.getenv("SMTP_TLS", "1") not in ("0", "false", "False")

    if not host or not user or not pwd:
        return False, "Variáveis SMTP_HOST/SMTP_USER/SMTP_PASS não configuradas."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = os.getenv("SMTP_FROM") or user
    msg["To"]      = ", ".join(to_addrs or [])
    if cc_addrs:
        msg["Cc"]  = ", ".join(cc_addrs)
    msg.set_content(body or "")

    if attach_path and attach_path.exists():
        data = attach_path.read_bytes()
        msg.add_attachment(data, maintype="application", subtype="zip", filename=attach_path.name)
    else:
        return False, "Anexo não encontrado para envio."

    try:
        if tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port) as server:
                server.starttls(context=context)
                server.login(user, pwd)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as server:
                server.login(user, pwd)
                server.send_message(msg)
        return True, "E-mail enviado."
    except Exception as e:
        return False, f"Falha no envio: {e}"

# 1) CORS: ajuste para o domínio REAL do seu front
FRONTEND_ORIGIN = "https://geraata-1.onrender.com"
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
# ------------------------- Fila real / PDFs / ZIP / E-mail -------------------
@api.get("/list_queue")
def list_queue():
    return {"success": True, "queue": _queue_snapshot()}

@api.post("/reset_queue")
def reset_queue():
    # limpa lista e apaga arquivos gerados
    for it in list(QUEUE):
        try:
            Path(it["path"]).unlink(missing_ok=True)
        except Exception:
            pass
    QUEUE.clear()
    try:
        ZIP_PATH.unlink(missing_ok=True)
    except Exception:
        pass
    return {"success": True}

@api.post("/queue_ata")
async def queue_ata(req: Request):
    """
    Gera um PDF com base no payload e adiciona à fila.
    Faz uma única chamada ao create_pdf e trata ambos cenários:
    - retorna bytes  -> salvamos em fpath
    - salva no disco -> verificamos fpath
    """
    import gerar_ata_core as core

    try:
        payload = await req.json()
    except Exception:
        form = await req.form()
        payload = dict(form)

    df_filt, colmap, df_base_tri = core.get_df_for_filters(
        ano=payload.get("ano"),
        turno=payload.get("turno"),
        turma=payload.get("turma"),
        trimestre=payload.get("trimestre"),
    )

    # Nome do arquivo padrão (com sanitização básica)
    numero_ata = str(payload.get("numero_ata") or "s-n").replace("/", "-").replace(":", "-")
    ano  = str(payload.get("ano") or "").strip()
    turm = str(payload.get("turma") or "").strip()
    turn = str(payload.get("turno") or "").strip()
    tri  = str(payload.get("trimestre") or "").strip()
    fname = f"ATA_{numero_ata}_{ano}_{turm}_{turn}_{tri}.pdf".replace(" ", "")
    fpath = OUT_DIR / fname

    # Chama o create_pdf UMA VEZ
    result = core.create_pdf(
        data=df_filt,
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
        override_text=payload.get("texto_editado") or payload.get("override_text"),
        df_base_tri=df_base_tri,
        column_map=colmap,
    )

    # 2) salvar o PDF retornado (BytesIO) no caminho esperado
    if isinstance(result, (bytes, bytearray)):
        fpath.write_bytes(result)
    elif isinstance(result, io.BytesIO):
        fpath.write_bytes(result.getvalue())
    else:
        # se sua função passar a salvar em disco futuramente, mantenha o fallback:
        if not fpath.exists():
            raise HTTPException(500, "create_pdf não retornou bytes e o arquivo esperado não foi encontrado.")

    item = {"filename": fpath.name, "path": str(fpath), "size": fpath.stat().st_size}
    QUEUE.append(item)
    return {"success": True, "queued": {"filename": fpath.name, "size": fpath.stat().st_size}}


@api.post("/finalize_and_send")
async def finalize_and_send(req: Request):
    # aceitar JSON ou FormData
    try:
        payload = await req.json()
    except Exception:
        form = await req.form()
        payload = dict(form)

    if not QUEUE:
        return {"success": False, "message": "Fila vazia."}

    # Cria ZIP
    try:
        with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
            for it in QUEUE:
                p = Path(it["path"])
                if p.exists():
                    z.write(p, arcname=p.name)
        zip_size = ZIP_PATH.stat().st_size
    except Exception as e:
        raise HTTPException(500, f"Falha ao zipar: {e}")

    # Envia e-mail
    to = payload.get("to")
    if not to:
        email = (payload.get("email") or "").strip()
        to = [email] if email else []

    cc = payload.get("cc") or []
    subject = payload.get("subject") or "Atas Conselho de Classe"
    body    = payload.get("body") or "Segue em anexo o arquivo .zip com as atas."
    ok, msg = _send_email_with_attachment(subject, body, to, ZIP_PATH, cc)

    return {"success": ok, "message": msg, "zip_size": ZIP_PATH.stat().st_size, "zip_name": ZIP_PATH.name}

@api.get("/download_zip")
def download_zip():
    if not ZIP_PATH.exists():
        raise HTTPException(404, "ZIP não encontrado. Gere com /finalize_and_send primeiro.")
    return FileResponse(path=str(ZIP_PATH), media_type="application/zip", filename=ZIP_PATH.name)

app.include_router(api)
