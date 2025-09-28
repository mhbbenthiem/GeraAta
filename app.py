# gerar_ata_core.py
from pathlib import Path
import os, io, re, json
from datetime import datetime
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from supabase import create_client, Client



# api/index.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, PlainTextResponse
from pathlib import Path
import tempfile, os, traceback


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

# ---------- PATHS ----------
BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR.parent / "public"
OBJETIVOS_JSON = os.getenv("OBJETIVOS_JSON", str(BASE_DIR / "data" / "objetivos.json"))
PARTICIPANTES_XLSX_PATH = Path(os.getenv("PARTICIPANTES_XLSX_PATH", BASE_DIR / "data" / "dados.xlsx"))
PARTICIPANTES_SHEET = os.getenv("PARTICIPANTES_SHEET", "profs")

# ---------- CACHE ----------
_participantes_cache = {"mtime": None, "lista": []}
_supabase_client = None
objetivos_map = {}

# ---------- MAPA DE COLUNAS ----------
COLUMN_MAP = {
    "ano": "ano",
    "turno": "turno",
    "turma": "turma",
    "trimestre": "trimestre",        # integer
    "aluno": "aluno",
    "materia": "materia",
    "descricao": "descricao",        # string (não lista)
    "papi": "papi",
    "inclusao": "inclusao",
    "perfil_turma": "perfilturma",
}

# ---------- ENV / SUPABASE ----------
def _get_env():
    return (
        os.getenv("SUPABASE_URL",""),
        os.getenv("SUPABASE_KEY",""),
        os.getenv("SUPABASE_TABLE","respostas"),
        os.getenv("SUPABASE_SCHEMA","public"),
    )

def get_supabase() -> Client:
    global _supabase_client
    url, key, *_ = _get_env()
    if not url or not key:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_KEY")
    if _supabase_client is None:
        _supabase_client = create_client(url, key)
    return _supabase_client

def fetch_supabase_df(ano=None, turno=None, turma=None, trimestre=None) -> pd.DataFrame:
    sb = get_supabase()
    url, key, table, schema = _get_env()
    q = sb.schema(schema).table(table).select("*")
    if ano not in (None, ""): q = q.eq("ano", str(ano))
    if turno not in (None, ""): q = q.eq("turno", str(turno))
    if turma not in (None, ""): q = q.eq("turma", str(turma))
    if trimestre not in (None, ""):
        try: q = q.eq("trimestre", int(str(trimestre).strip()))
        except ValueError: pass
    resp = q.execute()
    return pd.DataFrame(resp.data or [])

def get_df_for_filters(ano, turno, turma, trimestre):
    df_filt = fetch_supabase_df(ano=ano, turno=turno, turma=turma, trimestre=trimestre)
    if df_filt.empty:
        return df_filt, COLUMN_MAP, df_filt
    # base ampla do mesmo trimestre para “Integral”
    df_base_tri = fetch_supabase_df(ano=None, turno=None, turma=None, trimestre=trimestre)
    return df_filt, COLUMN_MAP, df_base_tri

# ---------- PARTICIPANTES ----------
def load_participantes_from_xlsx(force=False) -> list[str]:
    try:
        if not PARTICIPANTES_XLSX_PATH.exists():
            return []
        mtime = os.path.getmtime(PARTICIPANTES_XLSX_PATH)
        if (not force) and _participantes_cache["mtime"] == mtime and _participantes_cache["lista"]:
            return _participantes_cache["lista"]
        dfp = pd.read_excel(PARTICIPANTES_XLSX_PATH, sheet_name=PARTICIPANTES_SHEET, usecols=[0], header=0, dtype=str)
        serie = dfp.iloc[:,0].dropna()
        nomes, seen = [], set()
        for s in map(str.strip, serie):
            if not s or s.lower() in {"professor","professores","nome","participante"}: continue
            key = s.casefold()
            if key in seen: continue
            seen.add(key); nomes.append(s)
        _participantes_cache.update({"mtime": mtime, "lista": nomes})
        return nomes
    except Exception:
        return []

# ---------- TEXTO / FORMATADORES ----------
MESES = ["janeiro","fevereiro","março","abril","maio","junho","julho","agosto","setembro","outubro","novembro","dezembro"]

def numero_pt(n:int)->str:
    unidades=["zero","um","dois","três","quatro","cinco","seis","sete","oito","nove"]
    dez_a_dezenove=["dez","onze","doze","treze","catorze","quinze","dezesseis","dezessete","dezoito","dezenove"]
    dezenas=["","dez","vinte","trinta","quarenta","cinquenta","sessenta","setenta","oitenta","noventa"]
    centenas=["","cem","duzentos","trezentos","quatrocentos","quinhentos","seiscentos","setecentos","oitocentos","novecentos"]
    if n<10: return unidades[n]
    if n<20: return dez_a_dezenove[n-10]
    if n<100: d,u=divmod(n,10); return dezenas[d] if u==0 else f"{dezenas[d]} e {unidades[u]}"
    if n==100: return "cem"
    if n<1000: c,r=divmod(n,100); pref="cento" if c==1 else centenas[c]; return pref if r==0 else f"{pref} e {numero_pt(r)}"
    if n<10000: m,r=divmod(n,1000); mil="mil" if m==1 else f"{unidades[m]} mil"; return mil if r==0 else f"{mil} e {numero_pt(r)}"
    return str(n)

def ordinal_masc(n:int)->str: return f"{n}º"

def hora_por_extenso(hhmm:str)->str:
    h,m=map(int,hhmm.split(":"))
    horas="hora" if h==1 else "horas"
    if m==0: return f"{numero_pt(h)} {horas}"
    minutos="minuto" if m==1 else "minutos"
    return f"{numero_pt(h)} {horas} e {numero_pt(m)} {minutos}"

def data_por_extenso_long(data_str:str)->str:
    dt=datetime.strptime(data_str, "%Y-%m-%d")
    return f"Aos {numero_pt(dt.day)} de {MESES[dt.month-1]} de {numero_pt(dt.year)}"

def normaliza_ano_num(ano_str:str)->int:
    m=re.search(r"\d+", str(ano_str)); return int(m.group()) if m else int(ano_str)

def rotulo_trimestre(tri_str:str)->str:
    m=re.search(r"\d+", str(tri_str))
    if not m: return str(tri_str)
    return f"{ordinal_masc(int(m.group()))} trimestre"

def lista_para_texto(itens):
    itens=[i.strip() for i in itens if i and i.strip()]
    if not itens: return ""
    if len(itens)==1: return itens[0]
    return ", ".join(itens[:-1]) + " e " + itens[-1]

def ensure_ponto(s:str)->str:
    s=s.strip()
    return s if not s or s.endswith((".", "!", "?")) else s + "."

# ---------- OBJETIVOS ----------
def carregar_objetivos():
    global objetivos_map
    try:
        if os.path.isfile(OBJETIVOS_JSON):
            with open(OBJETIVOS_JSON, "r", encoding="utf-8") as f:
                objetivos_map = json.load(f)
        else:
            objetivos_map = {}
    except Exception:
        objetivos_map = {}

def objetivos_para_texto(ano:str, trimestre:str)->str:
    if not objetivos_map: carregar_objetivos()
    ano_n = normaliza_ano_num(ano)
    m = re.search(r"\d+", str(trimestre)); tri_n = int(m.group()) if m else None
    blocos=[]
    if str(ano_n) in objetivos_map and tri_n is not None and str(tri_n) in objetivos_map[str(ano_n)]:
        for disc, txt in objetivos_map[str(ano_n)][str(tri_n)].items():
            blocos.append(ensure_ponto(f"{disc}: {txt.strip()}"))
    return " ".join(blocos)

# ---------- INTEGRAL / AGRUPAÇÃO ----------
def filtra_integral_df(df_base_tri: pd.DataFrame, column_map: dict, ano_num:int, trimestre)->pd.DataFrame:
    df = df_base_tri.copy()
    tri_col = column_map["trimestre"]; ano_col = column_map["ano"]
    # Mantém mesmo trimestre; ano = “Integral” ou igual ao do filtro
    def _to_int(x):
        try: return int(str(x).strip())
        except: return None
    df = df[df[tri_col].map(_to_int) == _to_int(trimestre)]
    return df

def montar_partes_por_aluno(df_filt: pd.DataFrame, df_integral: pd.DataFrame, column_map: dict)->list[str]:
    alu_col = column_map["aluno"]; mat_col = column_map["materia"]; desc_col = column_map["descricao"]
    blocos=[]
    for aluno, g in df_filt.groupby(alu_col):
        pecas=[]
        for _, row in g.iterrows():
            materia = str(row.get(mat_col,"")).strip()
            desc = str(row.get(desc_col,"")).strip()
            if materia and desc:
                pecas.append(ensure_ponto(f"{materia}: {desc}"))
        if pecas:
            blocos.append(ensure_ponto(f"{aluno}: " + " ".join(pecas)))
    return blocos

# ---------- TEXTO COMPLETO ----------
def compose_text_core(df_filt, df_base_tri, column_map, numero_ata, data_reuniao, horario_inicio, horario_fim,
                      presidente, participantes, ano, turma, turno, trimestre)->str:
    ano_num = normaliza_ano_num(ano)
    tri_label = rotulo_trimestre(trimestre)
    turno_fmt = str(turno).strip().capitalize()
    participantes_lista = [p for p in str(participantes).split("\n") if p.strip()]
    participantes_corridos = lista_para_texto(participantes_lista)

    abertura = (
        f"Ata nº {numero_ata}. {data_por_extenso_long(data_reuniao)}, às {hora_por_extenso(horario_inicio)}, "
        f"a equipe da Escola Municipal Mirazinha Braga realizou o Conselho de Classe — {tri_label} do "
        f"{ordinal_masc(ano_num)} ano/turma {turma}, {turno_fmt}, com a participação de {participantes_corridos}. "
        f"O conselho de classe foi presidido por {presidente}, que deu início aos trabalhos informando aos participantes "
        f"que neste momento serão contempladas as reflexões sobre o entendimento dos processos vivenciados pelos estudantes "
        f"em relação à escolarização e à sua avaliação, tendo como documentos norteadores de análise e validação, "
        f"o Currículo do Ensino Fundamental – Diálogos com a BNCC (2020) e o planejamento do professor, "
        f"o qual compreendeu os seguintes objetivos: "
    )
    objetivos_txt = ensure_ponto(objetivos_para_texto(str(ano_num), str(trimestre))) or ""

    intro = (
        f"Em seguida, deu-se início às considerações sobre cada estudante do {ordinal_masc(ano_num)} ano/turma {turma}, "
        f"{turno_fmt}, referentes a {tri_label}. "
    )

    df_integral = filtra_integral_df(df_base_tri, column_map, ano_num, trimestre)
    blocos = montar_partes_por_aluno(df_filt, df_integral, column_map)
    estudantes_txt = (" ".join(blocos)).strip()

    encerramento = (
        f"Os encaminhamentos necessários serão retomados nos momentos de pós-conselho. "
        f"Nada mais havendo a tratar, eu {presidente}, na qualidade de presidente do conselho, "
        f"encerro a presente ata às {hora_por_extenso(horario_fim)}, que vai assinada por mim e pelos demais presentes."
    )
    return " ".join([abertura, objetivos_txt, intro, estudantes_txt, encerramento]).replace("  ", " ").strip()

# ---------- PDF ----------
def create_pdf(data: pd.DataFrame, numero_ata, data_reuniao, horario_inicio, horario_fim,
               presidente, participantes, ano, turma, turno, trimestre, override_text=None,
               df_base_tri: pd.DataFrame=None, column_map: dict=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle('HeaderStyle', parent=styles['Normal'], fontSize=11, alignment=TA_CENTER, spaceAfter=4)
    title_style  = ParagraphStyle('TitleStyle',  parent=styles['Normal'], fontSize=12, alignment=TA_CENTER, spaceAfter=10, fontName='Helvetica-Bold')
    normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontSize=10, alignment=TA_JUSTIFY, leading=14, spaceAfter=8)

    story=[]
    story.append(Paragraph("PREFEITURA MUNICIPAL DE CURITIBA", header_style))
    story.append(Paragraph("SECRETARIA MUNICIPAL DA EDUCAÇÃO", header_style))
    story.append(Paragraph("ESCOLA MUNICIPAL MIRAZINHA BRAGA", header_style))
    story.append(Spacer(1, 6))

    ano_num = normaliza_ano_num(ano)
    tri_label = rotulo_trimestre(trimestre)
    turno_fmt = str(turno).strip().capitalize()
    titulo = f"Conselho de Classe do {ordinal_masc(ano_num)} ano {turma} - {turno_fmt} - {tri_label}"
    story.append(Paragraph(titulo, title_style))

    participantes_lista = [p for p in str(participantes).split("\n") if p.strip()]
    participantes_corridos = lista_para_texto(participantes_lista)

    if override_text and override_text.strip():
        texto = override_text.strip()
    else:
        if df_base_tri is None or column_map is None:
            # fallback mínimo se não vierem de fora
            _, column_map, df_base_tri = get_df_for_filters(ano, turno, turma, trimestre)
        texto = compose_text_core(
            df_filt=data, df_base_tri=df_base_tri, column_map=column_map,
            numero_ata=numero_ata, data_reuniao=data_reuniao,
            horario_inicio=horario_inicio, horario_fim=horario_fim,
            presidente=presidente, participantes=participantes,
            ano=ano, turma=turma, turno=turno, trimestre=trimestre
        )

    story.append(Paragraph(texto.replace("\n","<br/>"), normal_style))
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>ASSINATURAS:</b>", normal_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph("_________________________________", normal_style))
    story.append(Paragraph(f"{presidente} — Presidente(a) do Conselho", normal_style))
    story.append(Spacer(1, 6))
    for participante in participantes_lista:
        story.append(Paragraph("_________________________________", normal_style))
        story.append(Paragraph(participante, normal_style))
        story.append(Spacer(1, 6))

    doc.build(story)
    buffer.seek(0)
    return buffer
