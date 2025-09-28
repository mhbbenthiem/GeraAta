from flask import request, jsonify
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
import io
from datetime import datetime
from email.message import EmailMessage
import re
import json
import dotenv
from supabase import create_client, Client
from pathlib import Path
import os

# pasta onde está este arquivo
BASE_DIR = Path(__file__).resolve().parent

# (opcional) diretório de arquivos públicos/estáticos, se você criou /public
PUBLIC_DIR = BASE_DIR / "public"

# Arquivos que antes vinham de resource_path(...)
OBJETIVOS_JSON = os.getenv("OBJETIVOS_JSON", str(BASE_DIR / "objetivos.json"))
PARTICIPANTES_XLSX_PATH = Path(os.getenv("PARTICIPANTES_XLSX_PATH", BASE_DIR / "dados.xlsx"))
PARTICIPANTES_SHEET = os.getenv("PARTICIPANTES_SHEET", "profs")

# Caches/Singletons usados no módulo
_participantes_cache = {"mtime": None, "lista": []}
_supabase_client = None

# Helper “compatível” com o antigo resource_path (se ainda quiser chamar)
def resource_path(rel: str) -> str:
    return str((BASE_DIR / rel).resolve())

html_path = PUBLIC_DIR / "HTML_ata.html"
html = html_path.read_text(encoding="utf-8")

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



def _get_env():
    url   = os.environ.get("SUPABASE_URL") or ""
    key   = os.environ.get("SUPABASE_KEY") or ""
    table = os.environ.get("SUPABASE_TABLE", "respostas")
    return url, key, table


def get_supabase() -> Client:
    global _supabase_client
    url, key, _ = _get_env()
    if not url or not key:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_KEY no .env")
    if _supabase_client is None:
        _supabase_client = create_client(url, key)
    return _supabase_client

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


