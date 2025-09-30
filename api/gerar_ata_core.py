from pathlib import Path
import os, io, re, json
from datetime import datetime
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

# Supabase (opcional)
try:
    from supabase import create_client, Client
except Exception:  # deixa rodar mesmo sem lib instalada
    create_client = None
    class Client: ...
    pass

def supabase_ping():
    # Faça um ping real se tiver SUPABASE_URL/KEY; por ora, retorne um status
    ok = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))
    info = {"SUPABASE_URL_set": bool(os.getenv("SUPABASE_URL")),
            "SUPABASE_KEY_set": bool(os.getenv("SUPABASE_KEY"))}
    return ok, info

# ---------- PATHS ----------
BASE_DIR = Path(__file__).resolve().parent
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
    "trimestre": "trimestre",
    "aluno": "aluno",
    "materia": "materia",
    "descricao": "descricao",
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

def _env_has_supabase():
    url, key, *_ = _get_env()
    return bool(url) and bool(key) and create_client is not None

def get_supabase() -> Client:
    if not _env_has_supabase():
        raise RuntimeError("SUPABASE_URL/KEY não definidos ou pacote supabase ausente.")
    global _supabase_client
    if _supabase_client is None:
        url, key, *_ = _get_env()
        _supabase_client = create_client(url, key)
    return _supabase_client

def fetch_supabase_df(ano=None, turno=None, turma=None, trimestre=None) -> pd.DataFrame:
    sb = get_supabase()
    _, _, table, schema = _get_env()
    q = sb.schema(schema).table(table).select("*")
    if ano not in (None, ""): q = q.eq("ano", str(ano))
    if turno not in (None, ""): q = q.eq("turno", str(turno))
    if turma not in (None, ""): q = q.eq("turma", str(turma))
    if trimestre not in (None, ""):
        try: q = q.eq("trimestre", int(str(trimestre).strip()))
        except ValueError: pass
    resp = q.execute()
    return pd.DataFrame(resp.data or [])

def fetch_local_df(ano=None, turno=None, turma=None, trimestre=None) -> pd.DataFrame:
    path = PARTICIPANTES_XLSX_PATH.parent / "dados.xlsx"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_excel(path, engine="openpyxl")
    # filtros simples
    def _eq(col, val):
        return df[col].astype(str).str.strip().str.casefold() == str(val).strip().casefold()
    if "ano" in df.columns and ano not in (None, ""): df = df[_eq("ano", ano)]
    if "turno" in df.columns and turno not in (None, ""): df = df[_eq("turno", turno)]
    if "turma" in df.columns and turma not in (None, ""): df = df[_eq("turma", turma)]
    if "trimestre" in df.columns and trimestre not in (None, ""):
        df = df[df["trimestre"].astype(str).str.contains(str(trimestre))]
    return df

def get_df_for_filters(ano, turno, turma, trimestre):
    if _env_has_supabase():
        try:
            df_filt = fetch_supabase_df(ano=ano, turno=turno, turma=turma, trimestre=trimestre)
            df_base_tri = fetch_supabase_df(ano=None, turno=None, turma=None, trimestre=trimestre)
        except Exception:
            df_filt = fetch_local_df(ano, turno, turma, trimestre)
            df_base_tri = fetch_local_df(None, None, None, trimestre)
    else:
        df_filt = fetch_local_df(ano, turno, turma, trimestre)
        df_base_tri = fetch_local_df(None, None, None, trimestre)
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
    tri_col = column_map["trimestre"]
    def _to_int(x):
        try: return int(str(x).strip())
        except: return None
    return df[df[tri_col].map(_to_int) == _to_int(trimestre)]

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
    if override_text and override_text.strip():
        texto = override_text.strip()
    else:
        if df_base_tri is None or column_map is None:
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

# ---------- SELF CHECK ----------
def core_self_check(root_dir: Path):
    """
    root_dir esperado: pasta root do deploy (no seu caso, 'src/').
    Retorna (ok, details) com chaves que o /health usa.
    """
    details = {}

    # Env Supabase
    url_set = bool(os.getenv("SUPABASE_URL"))
    key_set = bool(os.getenv("SUPABASE_KEY"))
    details["supabase_env"] = url_set and key_set   # <- nome que o /health usa
    details["SUPABASE_URL_set"] = url_set
    details["SUPABASE_KEY_set"] = key_set

    # Public (ajuda a depurar a home)
    public = root_dir / "public"
    details["public_html_ata_exists"] = (public / "HTML_ata.html").exists() or (public / "index.html").exists()

    # Data (respeita ENV se definido; senão, usa src/data)
    data_dir = root_dir / "data"
    objetivos_path = Path(os.getenv("OBJETIVOS_JSON") or (data_dir / "objetivos.json"))
    xlsx_path = Path(os.getenv("PARTICIPANTES_XLSX_PATH") or (data_dir / "dados.xlsx"))

    details["objetivos_json_path"] = str(objetivos_path)
    details["dados_xlsx_path"]     = str(xlsx_path)
    details["objetivos_json_exists"] = objetivos_path.exists()
    details["dados_xlsx_exists"]     = xlsx_path.exists()

    # Sinal verde geral: supabase configurada OU temos algum dado local
    ok = details["supabase_env"] or details["objetivos_json_exists"] or details["dados_xlsx_exists"]
    return ok, details

# ---------- DATAFRAME UTIL ----------

def load_all_df() -> pd.DataFrame:
    """Carrega todo o dataset (Supabase se disponível; caso contrário, Excel local)."""
    try:
        if _env_has_supabase():
            df = fetch_supabase_df(ano=None, turno=None, turma=None, trimestre=None)
        else:
            # usa o mesmo fallback local do get_df_for_filters
            df = fetch_local_df(None, None, None, None)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def _distinct_sorted(series) -> list:
    vals = (
        series.astype(str)
        .map(lambda s: s.strip())
        .replace({"": None})
        .dropna()
        .unique()
        .tolist()
    )
    try:
        # tenta ordenar numericamente se fizer sentido
        as_int = [int(str(x)) for x in vals]
        return [str(x) for x in sorted(as_int)]
    except Exception:
        return sorted(vals, key=lambda x: x.casefold())

def get_global_options() -> dict:
    """
    Retorna anos e turnos globais (para popular selects iniciais).
    """
    df = load_all_df()
    if df.empty:
        return {"anos": [], "turnos": []}
    anos = _distinct_sorted(df.get(COLUMN_MAP["ano"], pd.Series(dtype=str)))
    turnos = _distinct_sorted(df.get(COLUMN_MAP["turno"], pd.Series(dtype=str)))
    return {"anos": anos, "turnos": turnos}

def get_dependent_options(ano: str | None, turno: str | None) -> dict:
    """
    Dado ano/turno, retorna turmas e trimestres disponíveis.
    """
    df = load_all_df()
    if df.empty:
        return {"turmas": [], "trimestres": []}
    if ano:
        df = df[df[COLUMN_MAP["ano"]].astype(str).str.casefold()==str(ano).strip().casefold()]
    if turno:
        df = df[df[COLUMN_MAP["turno"]].astype(str).str.casefold()==str(turno).strip().casefold()]
    turmas = _distinct_sorted(df.get(COLUMN_MAP["turma"], pd.Series(dtype=str)))
    trimestres = _distinct_sorted(df.get(COLUMN_MAP["trimestre"], pd.Series(dtype=str)))
    # normaliza trimestres como inteiros quando possível
    try:
        trimestres = sorted({int(str(x)) for x in trimestres})
    except Exception:
        pass
    return {"turmas": turmas, "trimestres": trimestres}

def get_counts_summary() -> dict:
    """
    Resume contagens distintas (anos, turnos, turmas, trimestres) para /api/health.
    """
    df = load_all_df()
    if df.empty:
        return {"anos": 0, "turnos": 0, "turmas": 0, "trimestres": 0}
    return {
        "anos": df[COLUMN_MAP["ano"]].astype(str).str.strip().nunique() if COLUMN_MAP["ano"] in df else 0,
        "turnos": df[COLUMN_MAP["turno"]].astype(str).str.strip().nunique() if COLUMN_MAP["turno"] in df else 0,
        "turmas": df[COLUMN_MAP["turma"]].astype(str).str.strip().nunique() if COLUMN_MAP["turma"] in df else 0,
        "trimestres": df[COLUMN_MAP["trimestre"]].astype(str).str.strip().nunique() if COLUMN_MAP["trimestre"] in df else 0,
    }
