from flask import Flask, request, jsonify, send_file
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
import io
from datetime import datetime
import webbrowser
import threading
import time
import zipfile
import smtplib, ssl
from email.message import EmailMessage
import re
import json
import sys
from dotenv import load_dotenv, dotenv_values
from supabase import create_client, Client
from pathlib import Path
import certifi, os


# Corrige TLS no .exe
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

def app_dir() -> Path:
    # No .exe (frozen), pega a pasta do executável; no dev, a pasta do .py
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

BASE = app_dir()
CANDIDATES = [BASE / "api.env", BASE / ".env"]
ENV_PATH = next((p for p in CANDIDATES if p.exists()), CANDIDATES[0])

# Carrega .env (aceita UTF-8 com BOM do Notepad)
load_dotenv(ENV_PATH, override=True, encoding="utf-8-sig")

# Fallback explícito: injeta direto no os.environ mesmo que load_dotenv falhe
for k, v in dotenv_values(ENV_PATH, encoding="utf-8-sig").items():
    if v is not None:
        os.environ[k] = v

def _get_env():
    url   = os.environ.get("SUPABASE_URL") or ""
    key   = os.environ.get("SUPABASE_KEY") or ""
    table = os.environ.get("SUPABASE_TABLE", "respostas")
    return url, key, table


COLUMN_MAP = {
    "ano": "ano",
    "turno": "turno",
    "turma": "turma",
    "trimestre": "trimestre",            # integer no banco
    "aluno": "aluno",
    "materia": "materia",
    "descricao": ["descricao"],
    "papi": "papi",
    "inclusao": "inclusao",
    "perfil_turma": "perfilturma",       # (coluna perfilturma)
    "desc_candidates": ["descricao"],
    # você pode adicionar outras se o pipeline usar
    # "professor": "professor",
    # "funcao": "funcao",
}

_supabase_client: Client | None = None
def get_supabase() -> Client:
    global _supabase_client
    url, key, _ = _get_env()
    if not url or not key:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_KEY no .env")
    if _supabase_client is None:
        _supabase_client = create_client(url, key)
    return _supabase_client

import os

def fetch_supabase_df(ano=None, turno=None, turma=None, trimestre=None) -> pd.DataFrame:
    """
    Busca em {schema}.{table} com filtros opcionais.
    """
    sb = get_supabase()
    _, _, table = _get_env()
    schema = os.getenv("SUPABASE_SCHEMA", "public")

    # 👇 esta linha faltava
    q = sb.schema(schema).table(table).select("*")

    # filtros – use eq() só quando vier valor
    if ano not in (None, ""):
        q = q.eq("ano", str(ano))
    if turno not in (None, ""):
        q = q.eq("turno", str(turno))
    if turma not in (None, ""):
        q = q.eq("turma", str(turma))
    if trimestre not in (None, ""):
        try:
            tri_int = int(str(trimestre).strip())
            q = q.eq("trimestre", tri_int)   # no banco é integer
        except ValueError:
            pass

    resp = q.execute()
    data = resp.data or []
    return pd.DataFrame(data)


def get_df_for_filters(ano, turno, turma, trimestre):
    """
    Retorna:
      df_filt: linhas filtradas
      cm: COLUMN_MAP fixo
      df_base_tri: base do mesmo trimestre (para 'Integral', se seu pipeline usa)
    """
    df_filt = fetch_supabase_df(ano=ano, turno=turno, turma=turma, trimestre=trimestre)
    if df_filt.empty:
        return df_filt, COLUMN_MAP, df_filt

    # base mais ampla do mesmo trimestre (mesmo ano; turno/turma livres)
    df_base_tri = fetch_supabase_df(ano="Integral", turno=None, turma=None, trimestre=trimestre)
    if df_base_tri.empty:
        df_base_tri = fetch_supabase_df(ano=None, turno=None, turma=None, trimestre=trimestre)

    return df_filt, COLUMN_MAP, df_base_tri





app = Flask(__name__)

def resource_path(rel_path: str) -> str:
    # Quando “frozen” (PyInstaller onefile), os dados vão para sys._MEIPASS
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)

# ==== ESTADO GLOBAL ====
# --- Participantes via Excel ---
PARTICIPANTES_XLSX_PATH = resource_path("dados.xlsx")
PARTICIPANTES_SHEET = "profs"
PARTICIPANTES_COL = "A"
_participantes_cache = {"mtime": None, "lista": []}

current_data = None          # DataFrame carregado
column_map = {}              # Mapeamento de colunas (flexível p/ seu Excel)
QUEUE_DIR = None             # Pasta temp para PDFs/ZIP
queued_files = []            # Lista de PDFs já gerados (paths absolutos)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from datetime import datetime

MESES = ["janeiro","fevereiro","março","abril","maio","junho","julho","agosto","setembro","outubro","novembro","dezembro"]

def numero_pt(n: int) -> str:
    # 0..9999 por extenso (PT-BR simplificado p/ nossas datas/horas)
    unidades = ["zero","um","dois","três","quatro","cinco","seis","sete","oito","nove"]
    dez_a_dezenove = ["dez","onze","doze","treze","catorze","quinze","dezesseis","dezessete","dezoito","dezenove"]
    dezenas = ["","dez","vinte","trinta","quarenta","cinquenta","sessenta","setenta","oitenta","noventa"]
    centenas = ["","cem","duzentos","trezentos","quatrocentos","quinhentos","seiscentos","setecentos","oitocentos","novecentos"]

    if n < 10: return unidades[n]
    if 10 <= n < 20: return dez_a_dezenove[n-10]
    if 20 <= n < 100:
        d, u = divmod(n, 10)
        return dezenas[d] if u == 0 else f"{dezenas[d]} e {unidades[u]}"
    if 100 <= n < 1000:
        c, r = divmod(n, 100)
        if n == 100: return "cem"
        prefixo = "cento" if c == 1 else centenas[c]
        return prefixo if r == 0 else f"{prefixo} e {numero_pt(r)}"
    if 1000 <= n < 10000:
        m, r = divmod(n, 1000)
        mil = "mil" if m == 1 else f"{unidades[m]} mil"
        return mil if r == 0 else f"{mil} e {numero_pt(r)}"
    return str(n)

def ordinal_masc(n: int) -> str:
    # “1º”, “2º”, ...
    return f"{n}º"

def load_participantes_from_xlsx(force=False) -> list[str]:
    """
    Lê 'dados.xlsx' (aba 'profs', coluna A), ignora o cabeçalho e remove duplicados.
    Dedup case-insensitive, preservando a ordem original.
    """
    try:
        if not os.path.isfile(PARTICIPANTES_XLSX_PATH):
            return []

        mtime = os.path.getmtime(PARTICIPANTES_XLSX_PATH)
        if (not force) and _participantes_cache["mtime"] == mtime and _participantes_cache["lista"]:
            return _participantes_cache["lista"]

        # header=0 -> primeira linha é cabeçalho (ignora)
        dfp = pd.read_excel(
            PARTICIPANTES_XLSX_PATH,
            sheet_name=PARTICIPANTES_SHEET,
            usecols=[0],        # Coluna A
            header=0,           # <- importante: pula o cabeçalho
            dtype=str
        )

        # Série da primeira coluna
        serie = dfp.iloc[:, 0].dropna()

        nomes, seen = [], set()
        for val in serie:
            s = str(val).strip()
            if not s:
                continue

            # Proteção extra caso o cabeçalho venha parar nos dados
            if s.lower() in {"professor", "professores", "nome", "participante"}:
                continue

            key = s.casefold()  # dedup case-insensitive
            if key in seen:
                continue
            seen.add(key)
            nomes.append(s)

        _participantes_cache["mtime"] = mtime
        _participantes_cache["lista"] = nomes
        return nomes

    except Exception:
        return []


def hora_por_extenso(hhmm: str) -> str:
    h, m = hhmm.split(":")
    h, m = int(h), int(m)
    horas = "hora" if h == 1 else "horas"
    if m == 0:
        return f"{numero_pt(h)} {horas}"
    minutos = "minuto" if m == 1 else "minutos"
    return f"{numero_pt(h)} {horas} e {numero_pt(m)} {minutos}"

def data_por_extenso_long(data_str: str) -> str:
    # “aos vinte e um de agosto de dois mil e vinte e cinco”
    dt = datetime.strptime(data_str, "%Y-%m-%d")
    dia = numero_pt(dt.day)
    mes = MESES[dt.month-1]
    ano = numero_pt(dt.year)
    return f"Aos {dia} de {mes} de {ano}"

def normaliza_ano_num(ano_str: str) -> int:
    m = re.search(r"\d+", str(ano_str))
    return int(m.group()) if m else int(ano_str)

def rotulo_trimestre(tri_str: str) -> str:
    # aceita “2”, “2º”, “2º Trimestre”, etc.
    m = re.search(r"\d+", str(tri_str))
    if not m: 
        return str(tri_str)
    n = int(m.group())
    return f"{ordinal_masc(n)} trimestre"

def lista_para_texto(itens):
    itens = [i.strip() for i in itens if i and i.strip()]
    if not itens: return ""
    if len(itens) == 1: return itens[0]
    return ", ".join(itens[:-1]) + " e " + itens[-1]

def ensure_ponto(txt: str) -> str:
    t = (txt or "").strip()
    return t if not t else (t if t.endswith((".", "!", "?")) else t + ".")


# ======= UTIL: Inferir mapeamento de colunas =======
# Mapa do Integral por ano regular
INTEGRAL_MAP = {"2": "A", "3": "B", "4": "C"}

def normaliza(txt: str) -> str:
    return str(txt or "").strip().lower()


def filtra_integral_df(df, column_map, ano_num, trimestre):
    print("Filtrando para o integral")
    letra = INTEGRAL_MAP.get(str(ano_num))
    if not letra:
        return df.iloc[0:0]
    tri_col   = column_map["trimestre"]
    turma_col = column_map["turma"]
    ano_col   = column_map["ano"]

    return df[
        (df[tri_col].astype(str).str.strip().str.lower() == str(trimestre).strip().lower()) &
        (df[ano_col].astype(str).str.strip().str.lower() == "integral") &
        (df[turma_col].astype(str).str.strip().str.upper() == letra)
    ]

def montar_partes_por_aluno(dados_regular: pd.DataFrame, dados_integral: pd.DataFrame, column_map: dict) -> list[str]:
    print("Montando partes por aluno")
    alu_col = column_map["aluno"]
    dis_col = column_map["materia"]
    turma_col = column_map["turma"]

    blocos = []
    for aluno, grupo_regular in dados_regular.groupby(alu_col):
        partes = []

        # 1) Partes da turma regular
        for _, row in grupo_regular.iterrows():
            disciplina = str(row[dis_col]).strip()
            descricao = None
            for c in column_map["descricao"]:
                if c in row and pd.notna(row[c]) and str(row[c]).strip():
                    descricao = str(row[c]).strip()
                    break
            if descricao:
                partes.append(ensure_ponto(f"{disciplina}: {descricao}"))

        # 2) Partes da turma Integral (mesmo aluno)
        if not dados_integral.empty:
            grupo_int = dados_integral[dados_integral[alu_col].astype(str).str.strip().str.lower() == normaliza(aluno)]
            for _, row in grupo_int.iterrows():
                disciplina = str(row[dis_col]).strip()
                descricao = None
                for c in column_map["descricao"]:
                    if c in row and pd.notna(row[c]) and str(row[c]).strip():
                        descricao = str(row[c]).strip()
                        break
                if descricao:
                    turma = str(row[turma_col]).strip()
                    # LOG: verifica se é registro do Integral
                    if turma.lower().startswith("integral"):
                        print(f"[LOG] Aluno '{aluno}' tem registro do Integral: Disciplina='{disciplina}', Turma='{turma}', Descrição='{descricao}'")
                    partes.append(ensure_ponto(f"{disciplina}: {descricao}"))

        if partes:
            blocos.append(f"{aluno}: " + " ".join(partes))

    return blocos

def infer_column_map(df: pd.DataFrame) -> dict:
    cols = {c.lower(): c for c in df.columns}

    def pick(*cands, required=True, default=None):
        for c in cands:
            lc = c.lower()
            if lc in cols: 
                return cols[lc]
        if required:
            raise KeyError(f"Coluna não encontrada no Excel: {cands}")
        return default

    aluno = pick("Aluno")
    disciplina = pick("Materia")
    turma = pick("Turma")
    ano = pick("Ano")
    turno = pick("Turno")
    trimestre = pick("Trimestre")

    # possíveis colunas de descrição/parecer
    desc_candidates_raw = [
        "Descricao", "Descrição", "Descricao.1", "Descrição.1",
        "Comentario", "Comentário", "Observação", "Parecer",
        "Avaliação", "Desenvolvimento", "Acompanhamento", "Inclusao", "Inclusão",
        "PAPI", "PerfilTurma", "descricao"
    ]
    desc_candidates = [cols[c.lower()] for c in desc_candidates_raw if c.lower() in cols]

    return {
        "aluno": aluno,
        "disciplina": disciplina,
        "turma": turma,
        "ano": ano,
        "turno": turno,
        "trimestre": trimestre,
        "desc_candidates": desc_candidates,
    }



# ======= ROTA: Página inicial (HTML) =======
@app.route("/")
def index():
    return send_file(resource_path("HTML_ata.html"), as_attachment=False)


@app.route("/participants", methods=["GET"])
def participants():
    """
    Retorna JSON com a lista de participantes vindos do Excel.
    Parâmetro opcional: ?force=1 para recarregar ignorando cache.
    """
    try:
        force = str(request.args.get("force", "")).strip() in ("1", "true", "True")
        lista = load_participantes_from_xlsx(force=force)
        return jsonify({"success": True, "participants": lista})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    
@app.route("/health", methods=["GET"])
def health():
    # lê envs "ao vivo"
    url, key, table = _get_env()
    schema = os.getenv("SUPABASE_SCHEMA", "public")

    env_info = {
        "SUPABASE_URL_set": bool(url),
        "SUPABASE_KEY_set": bool(key),
        "SUPABASE_TABLE": table,
        "SUPABASE_SCHEMA": schema,
    }
    # infos extras de diagnóstico, se ENV_PATH existir no módulo
    try:
        if "ENV_PATH" in globals():
            p = ENV_PATH if isinstance(ENV_PATH, Path) else Path(str(ENV_PATH))
            env_info["env_path"] = str(p)
            env_info["env_exists"] = p.exists()
    except Exception:
        pass

    try:
        # valida/envia erro legível caso envs não estejam definidas
        sb = get_supabase()

        # ping simples
        ping = (
            sb.schema(schema)
              .table(table)
              .select("id")
              .limit(1)
              .execute()
        )
        sample = ping.data[0] if (ping.data and len(ping.data) > 0) else None

        # prévia para combos (não precisa tudo; limite leve)
        meta = (
            sb.schema(schema)
              .table(table)
              .select("ano, turno, turma, trimestre")
              .limit(1000)
              .execute()
        )
        df = pd.DataFrame(meta.data or [])

        def uniques_str(col: str):
            if col not in df.columns:
                return []
            s = df[col].dropna().map(lambda x: str(x).strip())
            return sorted(set([v for v in s if v != ""]))

        def uniques_int(col: str):
            if col not in df.columns:
                return []
            vals = []
            for v in df[col].dropna():
                try:
                    vals.append(int(str(v).strip()))
                except Exception:
                    pass
            return sorted(set(vals))

        values = {
            "anos": uniques_str("ano"),
            "turnos": uniques_str("turno"),
            "turmas": uniques_str("turma"),
            "trimestres": uniques_int("trimestre"),
        }

        counts = {
            "rows_previewed": int(len(df)),
            "anos": len(values["anos"]),
            "turnos": len(values["turnos"]),
            "turmas": len(values["turmas"]),
            "trimestres": len(values["trimestres"]),
        }

        return jsonify({
            "success": True,
            "status": "ok",
            "env_configured": env_info,
            "table_ping_ok": sample is not None,
            "sample_row": sample,
            "counts": counts,
            "values_preview": values
        }), 200

    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "status": "error",
            "env_configured": env_info,
            "error": str(e),
            "traceback": traceback.format_exc(limit=3)
        }), 200


# ======= ROTA: Opções dependentes (popular combos) =======
@app.route("/options", methods=["GET"])
def options():
    try:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            return jsonify({
                "success": False,
                "error": "SUPABASE_URL/SUPABASE_KEY ausentes. Verifique o arquivo .env."
            }), 200
        ano = request.args.get("ano")
        turno = request.args.get("turno")

        # Modo global: anos/turnos
        if not ano and not turno:
            df = fetch_supabase_df(ano=None, turno=None, turma=None, trimestre=None)
            if df.empty:
                return jsonify({"success": True, "anos": [], "turnos": []})

            anos = sorted(df.get("ano", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
            turnos = sorted(df.get("turno", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
            return jsonify({"success": True, "anos": anos, "turnos": turnos})

        # Modo filtrado: turmas/trimestres
        df = fetch_supabase_df(ano=ano, turno=turno, turma=None, trimestre=None)
        if df.empty:
            return jsonify({"success": True, "turmas": [], "trimestres": []})

        turmas = sorted(df.get("turma", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
        trimestres = sorted([str(x) for x in df.get("trimestre", pd.Series(dtype="Int64")).dropna().unique().tolist()])

        return jsonify({"success": True, "turmas": turmas, "trimestres": trimestres})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500




OBJETIVOS_JSON = resource_path("objetivos.json")
objetivos_map = {}

def _parse_objetivos_txt(path: str) -> dict:
    # Formato: [ano=1, trimestre=1] linhas “DISCIPLINA: texto...”
    cur_ano = cur_tri = None
    cur = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s: continue
            if s.startswith("[") and s.endswith("]"):
                # salva bloco anterior
                if cur_ano and cur_tri:
                    objetivos_map.setdefault(str(cur_ano), {})[str(cur_tri)] = cur
                # novo cabeçalho
                m_ano = re.search(r"ano\s*=\s*(\d+)", s, re.I)
                m_tri = re.search(r"trimestre\s*=\s*(\d+)", s, re.I)
                cur_ano = int(m_ano.group(1)) if m_ano else None
                cur_tri = int(m_tri.group(1)) if m_tri else None
                cur = {}
            else:
                if ":" in s and cur_ano and cur_tri is not None:
                    disc, txt = s.split(":", 1)
                    cur[disc.strip().upper()] = txt.strip()
    if cur_ano and cur_tri:
        objetivos_map.setdefault(str(cur_ano), {})[str(cur_tri)] = cur
    return objetivos_map

def carregar_objetivos():
    global objetivos_map
    try:
        if os.path.isfile(OBJETIVOS_JSON):
            with open(OBJETIVOS_JSON, "r", encoding="utf-8") as f:
                objetivos_map = json.load(f)
        else:
            objetivos_map = {}
    except Exception as e:
        print(f"⚠️ Erro ao carregar objetivos: {e}")
        objetivos_map = {}

def objetivos_para_texto(ano: str, trimestre: str) -> str:
    ano_n = normaliza_ano_num(ano)
    m = re.search(r"\d+", str(trimestre))
    tri_n = int(m.group()) if m else None
    if str(ano_n) in objetivos_map and tri_n is not None and str(tri_n) in objetivos_map[str(ano_n)]:
        blocos = []
        for disc, txt in objetivos_map[str(ano_n)][str(tri_n)].items():
            blocos.append(ensure_ponto(f"{disc}: {txt.strip()}"))
        return " ".join(blocos)
    return ""  # se não houver arquivo ou não houver bloco


# ======= Geração do PDF =======
def create_pdf(data: pd.DataFrame, numero_ata, data_reuniao, horario_inicio, horario_fim,
               presidente, participantes, ano, turma, turno, trimestre, override_text=None):
    """
    Gera o PDF com texto corrido. Se override_text vier preenchido,
    ele será usado como corpo da ata (permite edição do front).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)

    styles = getSampleStyleSheet()
    header_style = ParagraphStyle('HeaderStyle', parent=styles['Normal'], fontSize=11,
                                  alignment=TA_CENTER, spaceAfter=4)
    title_style = ParagraphStyle('TitleStyle', parent=styles['Normal'], fontSize=12,
                                 alignment=TA_CENTER, spaceAfter=10, fontName='Helvetica-Bold')
    normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontSize=10,
                                  alignment=TA_JUSTIFY, leading=14, spaceAfter=8)

    story = []
    # Cabeçalho fixo
    story.append(Paragraph("PREFEITURA MUNICIPAL DE CURITIBA", header_style))
    story.append(Paragraph("SECRETARIA MUNICIPAL DA EDUCAÇÃO", header_style))
    story.append(Paragraph("ESCOLA MUNICIPAL MIRAZINHA BRAGA", header_style))
    story.append(Spacer(1, 6))

    # Normalizações
    ano_num = normaliza_ano_num(ano)
    tri_label = rotulo_trimestre(trimestre)
    turno_fmt = str(turno).strip().capitalize()

    # Título
    titulo = f"Conselho de Classe do {ordinal_masc(ano_num)} ano {turma} - {turno_fmt} - {tri_label}"
    story.append(Paragraph(titulo, title_style))

    # Participantes
    participantes_lista = [p for p in participantes.split("\n") if p.strip()]
    participantes_corridos = lista_para_texto(participantes_lista)

    if override_text and override_text.strip():
        # Usa o texto editado pelo usuário
        texto = override_text.strip()
    else:
        # Monta automaticamente (mesma lógica do compose_text)
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
                # ===== INTEGRAL: carrega registros correspondentes ao ano =====
        ano_num = normaliza_ano_num(ano)
        df_integral = filtra_integral_df(current_data, column_map, ano_num, trimestre)

        # (já existia) participantes/abertura/etc.

        intro_estudantes = (
            f"Em seguida, deu-se início às considerações sobre cada estudante do {ordinal_masc(ano_num)} ano/turma {turma}, "
            f"{turno_fmt}, referentes a {tri_label}. "
        )

        alu_col = column_map["aluno"]; dis_col = column_map["disciplina"]
        blocos = montar_partes_por_aluno(data, df_integral, column_map)
        estudantes_txt = (" ".join(blocos)).strip()


        encerramento = (
            f"Os encaminhamentos necessários serão retomados nos momentos de pós-conselho. "
            f"Nada mais havendo a tratar, eu {presidente}, na qualidade de presidente do conselho, "
            f"encerro a presente ata às {hora_por_extenso(horario_fim)}, que vai assinada por mim e pelos demais presentes."
        )

        texto = " ".join([abertura, objetivos_txt, intro_estudantes, estudantes_txt, encerramento]).replace("  ", " ").strip()

    # Parágrafo principal (aceita \n → <br/>)
    story.append(Paragraph(texto.replace("\n","<br/>"), normal_style))
    story.append(Spacer(1, 10))

    # Assinaturas (Presidente + participantes)
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




def sanitize_filename(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_\-\.]+', '_', name)


@app.route("/compose_text", methods=["POST"])
def compose_text():
    try:
        payload = request.get_json(silent=True) or request.form or {}

        ano        = str(payload.get("ano", "")).strip()
        turno      = str(payload.get("turno", "")).strip()
        turma      = str(payload.get("turma", "")).strip()
        trimestre  = str(payload.get("trimestre", "")).strip()

        numero_ata     = str(payload.get("numero_ata", "")).strip()
        data_reuniao   = str(payload.get("data_reuniao", "")).strip()
        horario_inicio = str(payload.get("horario_inicio", "")).strip()
        horario_fim    = str(payload.get("horario_fim", "")).strip()
        presidente     = str(payload.get("presidente", "")).strip()
        participantes  = str(payload.get("participantes", "")).strip()

        campos_obrig = [ano, turno, turma, trimestre, numero_ata, data_reuniao,
                        horario_inicio, horario_fim, presidente, participantes]
        if any(not c for c in campos_obrig):
            return jsonify({"success": False, "error": "Preencha todos os campos e filtros."}), 400

        # ====== DADOS: SEMPRE SUPABASE ======
        # df_filt: somente as linhas do ano/turno/turma/trimestre
        # df_base_tri: base mais ampla do mesmo trimestre (serve para Integral)
        df_filt, cm, df_base_tri = get_df_for_filters(ano, turno, turma, trimestre)
        if df_filt.empty:
            return jsonify({"success": False, "error": "Nenhum dado encontrado para os filtros."}), 404

        # se o pipeline usa column_map global, fixe aqui
        global column_map
        column_map = cm

        # ====== TEXTO (igual ao PDF) ======
        ano_num = normaliza_ano_num(ano)
        tri_label = rotulo_trimestre(trimestre)
        turno_fmt = str(turno).strip().capitalize()

        participantes_lista = [p for p in participantes.split("\n") if p.strip()]
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

        intro_estudantes = (
            f"Em seguida, deu-se início às considerações sobre cada estudante do {ordinal_masc(ano_num)} ano/turma {turma}, "
            f"{turno_fmt}, referentes a {tri_label}. "
        )

        # ====== INTEGRAL ======
        # IMPORTANTE: aqui use a base ampla do trimestre (df_base_tri) vinda da Supabase,
        # e NÃO 'current_data' (que era do Excel).
        print("Chegou ate a hora de filtrar")
        df_integral = filtra_integral_df(df_base_tri, column_map, ano_num, trimestre)

        # ====== BLOCOS POR ALUNO ======
        # Observação: na sua tabela a coluna é 'materia' (não 'disciplina').
        # Se montar_partes_por_aluno usa column_map["materia"], já está ok
        print("Chegou montar partes por aluno")
        blocos = montar_partes_por_aluno(df_filt, df_integral, column_map)
        estudantes_txt = (" ".join(blocos)).strip()

        encerramento = (
            f"Os encaminhamentos necessários serão retomados nos momentos de pós-conselho. "
            f"Nada mais havendo a tratar, eu {presidente}, na qualidade de presidente do conselho, "
            f"encerro a presente ata às {hora_por_extenso(horario_fim)}, que vai assinada por mim e pelos demais presentes."
        )

        texto = " ".join([abertura, objetivos_txt, intro_estudantes, estudantes_txt, encerramento])
        texto = " ".join(texto.split())  # normaliza espaços

        return jsonify({"success": True, "texto": texto})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ======= ROTA: Adicionar ATA à fila (gera PDF e guarda no disco) =======
@app.route("/queue_ata", methods=["POST"])
def queue_ata():
    """
    Adiciona uma ata à fila:
    - recebe filtros (ano, turno, turma, trimestre) e dados da ata
    - busca SEMPRE na Supabase (sem Excel)
    - gera o PDF com texto corrido (ou usando 'texto_editado', se fornecido)
    - salva em QUEUE_DIR e adiciona em queued_files
    """
    # variáveis globais já existentes no seu app
    global queued_files, QUEUE_DIR, column_map

    try:
        # Aceita multipart/form-data (FormData) ou JSON
        payload = request.form if request.form else (request.get_json(silent=True) or {})
        texto_editado = str(payload.get("texto_editado", "")).strip()

        # --- Filtros ---
        ano        = str(payload.get("ano", "")).strip()
        turno      = str(payload.get("turno", "")).strip()
        turma      = str(payload.get("turma", "")).strip()
        trimestre  = str(payload.get("trimestre", "")).strip()

        # --- Dados da ata ---
        numero_ata     = str(payload.get("numero_ata", "")).strip()
        data_reuniao   = str(payload.get("data_reuniao", "")).strip()   # YYYY-MM-DD
        horario_inicio = str(payload.get("horario_inicio", "")).strip() # HH:MM
        horario_fim    = str(payload.get("horario_fim", "")).strip()    # HH:MM
        presidente     = str(payload.get("presidente", "")).strip()
        participantes  = str(payload.get("participantes", "")).strip()  # um por linha

        # Validação básica
        campos_obrig = [ano, turno, turma, trimestre, numero_ata, data_reuniao,
                        horario_inicio, horario_fim, presidente, participantes]
        if any(not c for c in campos_obrig):
            return jsonify({"success": False,
                            "error": "Preencha todos os campos e filtros (Ano, Turno, Turma, Trimestre, nº da ata, data, horários, presidente, participantes)."}), 400

        # Validação de data e horas
        from datetime import datetime
        try:
            datetime.strptime(data_reuniao, "%Y-%m-%d")
        except ValueError:
            return jsonify({"success": False, "error": "Data da reunião inválida. Use o formato YYYY-MM-DD."}), 400
        for tval, label in [(horario_inicio, "Início"), (horario_fim, "Término")]:
            try:
                datetime.strptime(tval, "%H:%M")
            except ValueError:
                return jsonify({"success": False, "error": f"Horário de {label} inválido. Use o formato HH:MM."}), 400

        # ====== DADOS: SEMPRE SUPABASE ======
        # df_filt: somente as linhas do ano/turno/turma/trimestre
        # df_base_tri: base mais ampla do mesmo trimestre (serve para Integral, se necessário em create_pdf)
        df_filt, cm, df_base_tri = get_df_for_filters(ano, turno, turma, trimestre)
        if df_filt.empty:
            return jsonify({"success": False, "error": "Nenhum dado encontrado para os filtros informados."}), 404

        # mantém compatibilidade com funções que consultam 'column_map'
        column_map = cm

        # Garante pasta temporária para fila
        if not QUEUE_DIR:
            # BASE_DIR deve existir no seu módulo; caso contrário, ajuste o caminho
            QUEUE_DIR = os.path.join(BASE_DIR, f"_atas_tmp_{int(time.time())}")
            os.makedirs(QUEUE_DIR, exist_ok=True)

        # ====== GERA PDF ======
        # Se você quiser que create_pdf também use df_base_tri/Integral, ajuste a função create_pdf
        # para aceitar o parâmetro opcional df_base_tri, ou mantenha como está se ela só precisa do df_filt.
        pdf_buffer = create_pdf(
            df_filt,
            numero_ata=numero_ata,
            data_reuniao=data_reuniao,
            horario_inicio=horario_inicio,
            horario_fim=horario_fim,
            presidente=presidente,
            participantes=participantes,
            ano=ano, turma=turma, turno=turno, trimestre=trimestre,
            override_text=(texto_editado or None)
        )

        # Salva em disco e adiciona na fila
        filename = f"ATA_{sanitize_filename(numero_ata)}.pdf"
        filepath = os.path.join(QUEUE_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(pdf_buffer.read())

        # Inicializa queued_files se ainda não existir
        if queued_files is None:
            queued_files = []
        queued_files.append({"path": filepath, "name": filename})

        return jsonify({
            "success": True,
            "queued": filename,
            "queue": [q["name"] for q in queued_files]
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



# ======= ROTA: Listar fila =======
@app.route("/list_queue", methods=["GET"])
def list_queue():
    return jsonify({"success": True, "queue": [q["name"] for q in queued_files]})

# ======= UTIL: Enviar e-mail =======
def send_email_with_attachment(to_email: str, subject: str, body: str, attachment_path: str):
    # Configure via variáveis de ambiente:
    # SMTP_SERVER, SMTP_PORT (ex: 587), SMTP_USER, SMTP_PASS, SENDER_EMAIL
    smtp_server = 'smtp.gmail.com'
    smtp_port = 587
    smtp_user = 'escolamirazinhapareceres@gmail.com'
    smtp_pass = 'rcgi glph cjyz elcn'
   

    if not all([smtp_server, smtp_port, smtp_user, smtp_pass]):
        raise RuntimeError("Variáveis de ambiente SMTP não configuradas para envio de e-mail.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.set_content(body)

    with open(attachment_path, "rb") as f:
        data = f.read()
    msg.add_attachment(data, maintype="application", subtype="zip", filename=os.path.basename(attachment_path))

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls(context=context)
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

# ======= ROTA: Finalizar e enviar ZIP =======
@app.route("/finalize_and_send", methods=["POST"])
def finalize_and_send():
    global queued_files, QUEUE_DIR
    try:
        if not queued_files:
            return jsonify({"success": False, "error": "A fila de atas está vazia."}), 400

        email_dest = (request.form.get("email") or (request.json or {}).get("email") or "").strip()
        if not email_dest:
            return jsonify({"success": False, "error": "Informe um e-mail válido."}), 400

        # Criar ZIP
        zipname = f"atas_conselho_{int(time.time())}.zip"
        zippath = os.path.join(QUEUE_DIR, zipname)
        with zipfile.ZipFile(zippath, "w", zipfile.ZIP_DEFLATED) as z:
            for item in queued_files:
                z.write(item["path"], arcname=item["name"])

        # Tentar enviar por e-mail; se falhar, retorna link de download
        try:
            send_email_with_attachment(
                to_email=email_dest,
                subject="Atas do Conselho de Classe",
                body="Segue em anexo o arquivo .zip com as atas geradas.",
                attachment_path=zippath
            )
            sent = True
            msg = "ZIP enviado por e-mail com sucesso!"
        except Exception as e:
            sent = False
            msg = f"Não foi possível enviar por e-mail automaticamente ({e}). Você pode baixar o ZIP pelo link."

        download_url = f"/download_zip?file={zipname}"

        # (Opcional) limpar fila após finalizar
        # queued_files = []

        return jsonify({"success": True, "email_sent": sent, "message": msg, "download_url": download_url, "zip_name": zipname})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ======= ROTA: Download do ZIP =======
@app.route("/download_zip", methods=["GET"])
def download_zip():
    file = request.args.get("file")
    if not file:
        return jsonify({"success": False, "error": "Arquivo não especificado"}), 400
    path = os.path.join(QUEUE_DIR, file)
    if not os.path.isfile(path):
        return jsonify({"success": False, "error": "Arquivo não encontrado"}), 404
    return send_file(path, as_attachment=True, download_name=file)

# ======= ROTA: Limpar fila =======
@app.route("/reset_queue", methods=["POST"])
def reset_queue():
    global queued_files
    queued_files = []
    return jsonify({"success": True})

# ======= Boot do servidor (como no seu script original) =======
def _verificar_servidor(host="127.0.0.1", port=5010, timeout=1):
    try:
        import socket as _socket
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False

def _iniciar_servidor():
    try:
        print("Iniciando servidor Flask...")
        carregar_objetivos()
        app.run(host="127.0.0.1", port=5010, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        print(f"✗ Erro ao iniciar servidor: {e}")

def _aguardar_e_abrir_navegador(url="http://127.0.0.1:5010/", max_tentativas=20):
    tentativas = 0
    print("Aguardando servidor ficar pronto...")
    while tentativas < max_tentativas:
        if _verificar_servidor():
            print("✓ Servidor detectado! Abrindo navegador...")
            time.sleep(1)
            webbrowser.open(url)
            return True
        tentativas += 1
        print(f"Tentativa {tentativas}/{max_tentativas}...")
        time.sleep(1)
    print("⚠️ Timeout ao aguardar servidor. Abrindo navegador assim mesmo...")
    webbrowser.open(url)
    return False

def main():
    print("=" * 50)
    print("SISTEMA DE GERAÇÃO DE ATAS - Conselho de Classe")
    print("=" * 50)
    print("🌐 Acesse: http://127.0.0.1:5010/")
    print("⭐ Pressione Ctrl+C para parar o servidor")

    servidor_thread = threading.Thread(target=_iniciar_servidor, daemon=True)
    servidor_thread.start()
    time.sleep(2)
    sucesso = _aguardar_e_abrir_navegador()
    if sucesso:
        print("✓ Sistema iniciado com sucesso!")
    else:
        print("⚠️ Sistema iniciado, porém sem confirmação de conectividade.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Sistema encerrado pelo usuário.")
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
    finally:
        print("Sistema encerrado.")

if __name__ == "__main__":
    main()