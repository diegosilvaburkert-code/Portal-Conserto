import json
import re
import unicodedata
from itertools import count
from html import escape as esc

import gspread
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ======================= CONFIGURAÇÃO =======================
LINK_PLANILHA = "https://docs.google.com/spreadsheets/d/1rZto32QSflOjbx9Gp9tC9geyrA9WzJ6tIHbX6zAnXsU"
NOME_DA_ABA = "Lead Time"

# Regras do fluxo
APROVACOES_OK = {"aprovado", "cortesia", "garantia"}      # relatório aprovado -> segue para reparo
APROVACOES_REPROVADAS = {"reprovado"}                      # relatório reprovado -> sem reparo, vai para devolução
APROVACOES_SEM_PEDIDO = {"cortesia", "garantia"}           # não exigem Aceite de Pedido (Ordem SAP)
CONCLUIDOS = {"concluido", "finalizado"}                   # "Status de conclusão" que ENCERRA o caso
ALERTA_AMARELO_DIAS = 30                                   # dias desde o recebimento
ALERTA_VERMELHO_DIAS = 60

st.set_page_config(page_title="Hub de Conserto", page_icon="🛠️", layout="wide")

# ======================= ESTADO DA SIDEBAR (EXPANDIDO / RECOLHIDO) =======================
if "sidebar_expanded" not in st.session_state:
    st.session_state["sidebar_expanded"] = True

expandido = st.session_state["sidebar_expanded"]
sb_width = "270px" if expandido else "90px"

# ======================= ESTILOS CSS (PADRÃO LOGÍSTICA / PIONEERS) =======================
st.markdown(f"""
    <style>
        :root {{
            --azul: #6d8fb6;
            --azul-esc: #3b719f;
            --prata: #b8bdc6;
        }}
        .stApp {{
            background: linear-gradient(135deg, #eef3f8 0%, #dce6f1 100%);
        }}
        header[data-testid="stHeader"] {{
            background: transparent !important;
        }}
        .block-container {{
            padding-top: 1.6rem;
            max-width: 100% !important;
            padding-left: 1.2rem;
            padding-right: 1.2rem;
        }}
        .header-banner {{
            position: relative; overflow: hidden;
            background: linear-gradient(160deg, #6d8fb6 0%, #5681ac 48%, #3b719f 100%);
            border-radius: 26px;
            padding: 34px 40px 36px;
            box-shadow: 0 16px 36px rgba(44, 90, 130, .30);
            margin-bottom: 26px;
        }}
        .header-banner::before {{
            content: ""; position: absolute; right: -36px; top: -46px; width: 250px; height: 250px; border-radius: 50%;
            background: radial-gradient(circle at 34% 28%, #f6f8fb 0%, #c9ced6 38%, #8a919d 100%);
            box-shadow: inset -14px -18px 34px rgba(31, 58, 87, .35); opacity: .92;
        }}
        .header-banner::after {{
            content: ""; position: absolute; right: 200px; bottom: -42px; width: 120px; height: 120px; border-radius: 50%;
            background: radial-gradient(circle at 34% 28%, rgba(255,255,255,.75), rgba(184,189,198,.5) 55%, rgba(255,255,255,.10));
            box-shadow: inset -8px -10px 22px rgba(31, 58, 87, .25);
        }}
        .header-banner > * {{ position: relative; z-index: 1; }}
        .stApp .header-banner .hb-kicker {{
            font-size: 12px; font-weight: 800; letter-spacing: 3px; color: #dbe6f1; text-align: left !important; margin: 0 0 10px 0 !important;
        }}
        .stApp .header-banner h1 {{
            color: #ffffff !important; font-size: 46px !important; line-height: 1.02 !important; margin: 0 !important;
            letter-spacing: .5px; font-weight: 800 !important; text-transform: uppercase;
            text-align: left !important; max-width: 70%;
        }}
        .stApp .header-banner p {{
            margin: 12px 0 0 0 !important; font-size: 16px; color: #eaf1f8; opacity: .95;
            text-align: left !important; max-width: 62%;
        }}
        @media (max-width: 900px) {{
            .header-banner::before, .header-banner::after {{ display: none; }}
            .stApp .header-banner h1 {{ font-size: 30px !important; max-width: 100%; }}
            .stApp .header-banner p {{ max-width: 100%; }}
        }}

        section[data-testid="stSidebar"] {{
            width: {sb_width} !important;
            min-width: {sb_width} !important;
            max-width: {sb_width} !important;
            transform: none !important;
            visibility: visible !important;
            background: linear-gradient(185deg, #5681ac 0%, #3b719f 55%, #2c5a82 100%) !important;
            border-radius: 0 28px 28px 0;
            box-shadow: 8px 0 30px rgba(44, 90, 130, .28);
            transition: width 0.3s ease, min-width 0.3s ease, max-width 0.3s ease;
            position: relative !important;
        }}
        section[data-testid="stSidebar"] > div {{ background: transparent !important; }}

        [data-testid="stSidebarCollapseButton"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="collapsedControl"],
        [data-testid="stSidebarResizeHandle"] {{
            display: none !important;
        }}

        [data-testid="stSidebarUserContent"] {{
            padding: 1.2rem 0.8rem 3.5rem 0.8rem !important;
        }}

        .sb-header {{
            display: flex; align-items: center;
            justify-content: {"space-between" if expandido else "center"};
            padding: 4px 6px 16px 6px;
            border-bottom: 1px solid rgba(255,255,255,.22);
            margin-bottom: 20px;
        }}
        .sb-logo {{
            display: flex; align-items: center; gap: 10px; white-space: nowrap; font-size: 24px;
        }}
        .sb-logo span {{
            font-size: 12px; font-weight: 800; letter-spacing: 1.6px; line-height: 1.2; color: #ffffff;
        }}

        section[data-testid="stSidebar"] .stButton {{ margin-bottom: 8px; }}
        section[data-testid="stSidebar"] .stButton button {{
            width: 100%;
            justify-content: center !important;
            border: none;
            border-radius: 16px;
            padding: {"0.8rem 1rem" if expandido else "0.8rem 0rem"} !important;
            background: transparent;
            box-shadow: none;
            transition: all 0.2s;
        }}
        .stApp section[data-testid="stSidebar"] .stButton button p {{
            text-align: {"left" if expandido else "center"} !important;
            font-size: {"15px" if expandido else "20px"};
            font-weight: 700;
            color: #e3ecf5;
            margin: 0;
        }}
        section[data-testid="stSidebar"] .stButton button:hover {{ background: rgba(255,255,255,.14); }}
        section[data-testid="stSidebar"] .stButton button[kind="primary"],
        section[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"] {{
            background: #ffffff;
            box-shadow: 0 10px 22px rgba(20, 50, 80, .30);
        }}
        .stApp section[data-testid="stSidebar"] .stButton button[kind="primary"] p,
        .stApp section[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"] p {{
            color: #2c5a82 !important;
        }}

        .sidebar-status-container {{
            margin-top: 28px; text-align: center;
            background: rgba(255,255,255,.14); color: #ffffff;
            padding: 7px 10px; border-radius: 12px;
            font-size: 11px; font-weight: 700; letter-spacing: .4px;
            border: 1px solid rgba(255,255,255,.30); white-space: nowrap;
        }}

        .stApp h2, .stApp h3, .stApp h4, .stApp p, .stMarkdown p {{ text-align: center !important; }}
        .stAlert {{ border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center !important; }}
        [data-testid="stWidgetLabel"] {{ text-align: center !important; display: block !important; width: 100% !important; }}
        [data-testid="stTextInput"] div[data-baseweb="input"] {{ max-width: 560px !important; margin: 0 auto !important; }}
        [data-testid="stTextInput"] input {{ text-align: center !important; }}

        .stForm [data-testid="stWidgetLabel"] label p, 
        .stRadio [data-testid="stWidgetLabel"] label p,
        .stSelectbox [data-testid="stWidgetLabel"] label p,
        .stTextInput [data-testid="stWidgetLabel"] label p {{
            color: #1f3a57 !important;
            font-weight: 600 !important;
            font-size: 13.5px !important;
        }}

        .form-section-card {{
            background: #ffffff;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 4px 20px rgba(59, 113, 159, 0.08);
            border: 1px solid #e2e8f0;
            text-align: left !important;
        }}
        
        [data-testid="stForm"] {{
            background: #ffffff; border: 1px solid #dfe7f0 !important; border-radius: 22px; padding: 26px 28px;
            box-shadow: 0 10px 28px rgba(44, 90, 130, .10);
        }}
        .form-title {{
            font-size: 15px; font-weight: 800; letter-spacing: .8px; text-transform: uppercase;
            color: #3b719f; margin: 8px 0 14px; text-align: left !important;
            padding-bottom: 8px; border-bottom: 2px solid #e6eef6;
        }}

        .stTextInput input, .stSelectbox div[data-baseweb="select"] {{
            background-color: #ffffff !important;
            border: 1px solid #cbd5e1 !important;
            border-radius: 10px !important;
            color: #1e293b !important;
        }}
        .stTextInput input:focus, .stSelectbox div[data-baseweb="select"]:focus-within {{
            border-color: #3b719f !important;
            box-shadow: 0 0 0 3px rgba(59, 113, 159, 0.15) !important;
        }}

        .stFormSubmitButton button {{
            background: linear-gradient(90deg, #3b719f, #6d8fb6) !important;
            color: white !important;
            font-weight: 700 !important;
            border-radius: 12px !important;
            padding: 0.75rem 1.5rem !important;
            border: none !important;
            box-shadow: 0 4px 15px rgba(59, 113, 159, 0.3) !important;
            transition: all 0.2s ease;
            width: 100%;
        }}
        .stFormSubmitButton button:hover {{
            opacity: 0.95;
            transform: translateY(-1px);
        }}
    </style>
""", unsafe_allow_html=True)


# ======================= COLUNAS DA PLANILHA =======================
CAMPOS = {
    "ordem_status": (("ordem", "status"), 0),
    "cliente": (("nome", "cliente"), 1),
    "nf": (("numero", "nota fiscal"), 3),
    "itens": (("itens",), 4),
    "data_nf": (("data", "nota fiscal", "origem"), 5),
    "data_receb": (("data", "recebimento"), 6),
    "data_crm": (("data", "registro"), 7),
    "cas": (("cas-so",), 8),
    "service": (("service", "notification"), 9),
    "data_verif": (("data final",), 10),
    "status_verif": (("status", "verifica"), 11),
    "aprovacao": (("aprova",), 12),
    "aceite": (("aceite",), 13),
    "data_ordem": (("data", "ordem"), 14),
    "data_reparo": (("data", "reparo"), 15),
    "solic_devol": (("solicita",), 16),
    "status_devol": (("status", "devolu"), 17),
    "status_concl": (("status", "conclus"), 18),
}
LABEL = {
    "ordem_status": "Ordem Status", "cliente": "Nome do cliente", "nf": "Nº da Nota Fiscal",
    "itens": "Itens da nota", "data_nf": "Data da NF de origem", "data_receb": "Data de recebimento",
    "data_crm": "Data de Registro (CRM)", "cas": "CAS-SO-BR (CRM)", "service": "Service Notification",
    "data_verif": "Data final da verificação", "status_verif": "Status de Verificação",
    "aprovacao": "Aprovação de relatório", "aceite": "Aceite de Pedido (Ordem SAP)",
    "data_ordem": "Data da Ordem do Pedido", "data_reparo": "Data Reparo Finalizado",
    "solic_devol": "Solicitação de devolução", "status_devol": "Status de devolução",
    "status_concl": "Status de conclusão",
}
MES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
NOMES_FASES = ["Registro", "Recebimento Logística", "Recebimento Serviços", "Verificação",
               "Aprovação", "Reparo", "Devolução", "Conclusão"]
COR_GRUPO = {
    "A fazer": "#5681ac", "Follow up": "#e0a800", "Importação": "#6850d8",
    "Garantia": "#6aa84f", "Reprovado": "#e53935", "Aprovado": "#1e8e3e",
    "Finalizados": "#6b7280", "Dados incompletos": "#9aa7ba"
}


def _norm(t):
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def parse_data(s):
    s = str(s).strip().lower()
    if not s:
        return pd.NaT
    m = re.match(r"^(\d{1,2})[-/ ]+([a-zç]{3})\.?[-/ ]+(\d{2,4})", s)
    if m and m.group(2) in MES:
        ano = int(m.group(3))
        ano += 2000 if ano < 100 else 0
        try:
            d = pd.Timestamp(ano, MES[m.group(2)], int(m.group(1)))
        except ValueError:
            return pd.NaT
    else:
        d = pd.to_datetime(s, dayfirst=True, errors="coerce", format="mixed")
    return d if pd.notna(d) and d.year >= 2020 else pd.NaT


# ======================= REGRAS DO FLUXO =======================
def avaliar(r):
    f = lambda k: bool(r[k])
    falta = lambda ks: [LABEL[k] for k in ks if not r[k]]
    ap = _norm(r["aprovacao"])
    reprov = ap in APROVACOES_REPROVADAS
    dispensa = ap in APROVACOES_SEM_PEDIDO
    concl = _norm(r["status_concl"]) in CONCLUIDOS

    req2, req3 = ["nf", "data_receb"], ["data_crm", "cas", "service"]
    p1 = f("cliente")
    p2 = p1 and not falta(req2)
    p3 = p2 and not falta(req3)
    p4 = p3 and f("data_verif")
    p5 = p4 and ap in APROVACOES_OK
    bloq = p4 and reprov
    p6 = p5 and f("data_reparo") and (dispensa or f("aceite"))
    p7 = (p6 or bloq) and f("solic_devol")
    p8 = p7 and concl

    est = ["ok" if x else "pendente" for x in (p1, p2, p3, p4, p5, p6, p7, p8)]
    if bloq:
        est[4], est[5] = "bloqueado", "nao_realizado"
    fora = (f("data_reparo") and not (p5 or bloq)) or (f("solic_devol") and not (p6 or bloq))
    aviso = " ⚠️ Há campos preenchidos fora da sequência do fluxo; confira a planilha." if fora else ""

    final = p1 and concl
    if final:
        feito = [p1, not falta(req2), not falta(req3), f("data_verif"), ap in APROVACOES_OK,
                 f("data_reparo") and (dispensa or f("aceite")),
                 f("solic_devol") or _norm(r["status_devol"]) == "devolvido"]
        est = ["ok" if x else "pulado" for x in feito] + ["ok"]
        if reprov:
            est[4], est[5] = "bloqueado", "nao_realizado"
        nao_feitas = [NOMES_FASES[i] for i in range(1, 7) if est[i] == "pulado"]
        aviso = ""

    def res(status, grupo, detalhe, faltam=()):
        return {"estados": est, "status": status, "grupo": grupo, "detalhe": detalhe + aviso,
                "faltam": list(faltam), "concluido": bool(p8 or final)}

    if final:
        if reprov:
            d = "Relatório reprovado: equipamento devolvido sem reparo. Caso finalizado."
        else:
            d = "Caso finalizado."
            d += (" Etapas não realizadas (desconsideradas): " + ", ".join(nao_feitas) + ".") if nao_feitas else " Todas as etapas foram concluídas."
        if r["status_devol"]:
            d += f" Status da devolução: {r['status_devol']}."
        return res("🟢 Finalizado", "Finalizados", d)

    def devolucao():
        extra = f" Status da devolução: {r['status_devol']}." if r["status_devol"] else ""
        extra += f" Status de conclusão: {r['status_concl']}." if r["status_concl"] else ""
        return res("🚚 Devolução em andamento", "Follow up",
                   "Devolução solicitada." + extra, falta(["status_concl"]))

    if not p1:
        return res("⚪ Dados incompletos", "Dados incompletos", "O registro não tem o nome do cliente.", falta(["cliente"]))
    if not p2:
        return res("⚪ Aguardando recebimento (Logística)", "A fazer",
                   "Cliente registrado. Falta confirmar o recebimento do material na Logística.", falta(req2))
    if not p3:
        return res("🟡 Aguardando registro no CRM (Serviços)", "A fazer",
                   "Material recebido pela Logística. Aguardando o registro no CRM e a Service Notification pelo setor de Serviços.",
                   falta(req3))
    if not p4:
        return res("🔵 Em verificação / diagnóstico", "A fazer",
                   "Registro concluído. O equipamento está em verificação.", falta(["data_verif"]))
    if not (p5 or bloq):
        atual = f" Valor atual na planilha: '{r['aprovacao']}'." if r["aprovacao"] else ""
        return res("🟠 Aguardando aprovação do relatório", "Follow up",
                   f"Verificação concluída em {r['data_verif']}. Aguardando a decisão do relatório "
                   f"(Aprovado, Cortesia, Garantia ou Reprovado).{atual}", falta(["aprovacao"]))
    if bloq:
        if not p7:
            return res("🔴 Relatório reprovado — aguardando solicitação de devolução", "Reprovado",
                       "Relatório reprovado: não haverá reparo. Falta solicitar a devolução do equipamento.",
                       falta(["solic_devol"]))
        return devolucao() if not p8 else res("🟢 Concluído", "Finalizados", "Equipamento devolvido sem reparo. Processo concluído.")
    if not p6:
        if not dispensa and not f("aceite"):
            return res("🟠 Aguardando aceite do pedido (Ordem SAP)", "Follow up",
                       f"Relatório {r['aprovacao']}. Falta o aceite do pedido (Ordem SAP) para iniciar o reparo.",
                       falta(["aceite"]))
        return res("🛠️ Em reparo", "Importação",
                   f"Relatório {r['aprovacao']}"
                   + (" (sem pedido)" if dispensa else f"; pedido {r['aceite']} aceito") + ". Equipamento em reparo.",
                   falta(["data_reparo"]))
    if not p7:
        return res("📦 Reparo finalizado — aguardando solicitação de devolução", "Follow up",
                   f"Reparo finalizado em {r['data_reparo']}. Falta solicitar a devolução ao cliente.",
                   falta(["solic_devol"]))
    if not p8:
        return devolucao()
    return res("🟢 Concluído", "Finalizados", "Equipamento reparado, devolvido e processo concluído.")


# ======================= LEITURA E ESCRITA DA PLANILHA =======================
@st.cache_resource
def conectar():
    info_json = st.secrets["gspread_credentials"]["json_string"]
    dic_chaves = json.loads(info_json, strict=False)
    if "private_key" in dic_chaves:
        dic_chaves["private_key"] = dic_chaves["private_key"].replace("\\\\n", "\n").replace("\\n", "\n")
    return gspread.service_account_from_dict(dic_chaves)


@st.cache_data(ttl=300)
def ler_valores():
    return conectar().open_by_url(LINK_PLANILHA).worksheet(NOME_DA_ABA).get_all_values()


def ler_casos(valores, hoje):
    ini, cab = 4, None
    for i, l in enumerate(valores[:20]):
        t = _norm(" ".join(l))
        if "nome do cliente" in t and "nota fiscal" in t:
            ini, cab = i + 1, [_norm(c) for c in l]
            break
    idx, ausentes = {}, []
    for k, (termos, padrao) in CAMPOS.items():
        j = padrao if cab is None else next((j for j, h in enumerate(cab) if all(t in h for t in termos)), None)
        idx[k] = j
        if j is None:
            ausentes.append(LABEL[k])
    linhas = []
    for row_idx, l in enumerate(valores[ini:], start=ini + 1):
        reg = {k: (l[j].strip() if j is not None and j < len(l) else "") for k, j in idx.items()}
        reg = {k: ("" if v in ("-", "—") else v) for k, v in reg.items()}
        reg["_row"] = row_idx
        if reg["cliente"] or reg["nf"]:
            linhas.append(reg)
    df = pd.DataFrame(linhas)

    av = [avaliar(r) for r in df.to_dict("records")]
    df["status"] = [a["status"] for a in av]
    df["grupo"] = [a["grupo"] for a in av]
    df["concluido"] = [a["concluido"] for a in av]
    df["_aval"] = av
    dt = df["data_receb"].map(parse_data)
    df["dias"] = [(hoje - d).days if pd.notna(d) and not c else None for d, c in zip(dt, df["concluido"])]
    return df, ausentes


def salvar_caso_planilha(dados_form, row_num=None):
    try:
        sh = conectar().open_by_url(LINK_PLANILHA).worksheet(NOME_DA_ABA)
        valores = sh.get_all_values()
        
        ini, cab = 4, None
        for i, l in enumerate(valores[:20]):
            t = _norm(" ".join(l))
            if "nome do cliente" in t and "nota fiscal" in t:
                ini, cab = i + 1, [_norm(c) for c in l]
                break
        
        idx = {}
        for k, (termos, padrao) in CAMPOS.items():
            j = padrao if cab is None else next((j for j, h in enumerate(cab) if all(t in h for t in termos)), None)
            idx[k] = j

        max_col = max([v for v in idx.values() if v is not None], default=18)
        nova_linha = [""] * (max_col + 1)
        for k, j in idx.items():
            if j is not None and k in dados_form:
                nova_linha[j] = str(dados_form[k])

        if row_num:
            cell_range = f"A{row_num}:R{row_num}"
            sh.update(cell_range, [nova_linha[:len(CAMPOS)]])
            st.cache_data.clear()
            return True, "Caso atualizado com sucesso na planilha!"
        else:
            proxima_linha = len(valores) + 1
            sh.insert_row(nova_linha[:len(CAMPOS)], index=proxima_linha)
            st.cache_data.clear()
            return True, "Novo caso inserido com sucesso na planilha!"
    except Exception as e:
        return False, f"Erro ao gravar na planilha: {e}"


# ======================= VISUAL (HTML) =======================
def compacta(h):
    return "\n".join(l.strip() for l in h.splitlines() if l.strip())


# CSS padronizado idêntico ao do Conserto/Reparo (.tl-wrap, .tl-item.cur, etc.)
CSS_TL = """<style>
.tl-wrap{position:relative;display:flex;justify-content:space-between;margin:26px 0 6px}
.tl-wrap::before{content:"";position:absolute;top:16px;left:0;width:100%;height:12px;background:#dbe5f0;border-radius:10px;z-index:1}
.tl-seg{position:absolute;top:16px;height:12px;z-index:2}
.tl-item{position:relative;display:flex;flex-direction:column;align-items:center;flex:1;z-index:3;min-width:0}
.tl-c{width:44px;height:44px;border-radius:50%;box-sizing:border-box;background:#fff;border:4px solid #cbd6e2;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:18px;color:#fff;box-shadow:0 2px 6px rgba(0,0,0,.10)}
.tl-n{margin-top:9px;font-size:12.5px;font-weight:600;color:#6b7a90;text-align:center;padding:0 4px;line-height:1.25}
.tl-t{margin-top:5px;min-height:18px;font-size:10.5px;font-weight:800;letter-spacing:.8px;text-transform:uppercase;color:#3b719f}
.tl-item.completed .tl-c{background:#2fae6e;border-color:#2fae6e}
.tl-item.completed .tl-n{color:#2c5a82;font-weight:700}
.tl-item.cur .tl-c{border-color:#3b719f;box-shadow:0 0 0 6px rgba(59,113,159,.16),0 2px 6px rgba(0,0,0,.10)}
.tl-item.cur .tl-c::after{content:"";width:14px;height:14px;border-radius:50%;background:#3b719f}
.tl-item.cur .tl-n{color:#2c5a82;font-weight:800}
.tl-item.blocked .tl-c{background:#e53935;border-color:#e53935}
.tl-item.blocked .tl-n{color:#e53935;font-weight:800}
.tl-item.blocked .tl-t{color:#e53935}
.tl-item.warn .tl-c{background:#f5b301;border-color:#f5b301;color:#fff}
.tl-item.warn .tl-n{color:#b7791f;font-weight:700}
.tl-item.skipped .tl-c{background:#e1e9f5;border-color:#c5d3e8;color:#7d8fa8}
</style>"""

COR_TRECHO = {"ok": "#2fae6e", "bloqueado": "#e53935", "nao_realizado": "#f5b301", "pulado": "#c5d3e8"}


def html_timeline(estados):
    n = len(estados)
    # Identifica a primeira etapa pendente (que será marcada como 'cur' / em andamento)
    atual_idx = next((i for i, e in enumerate(estados) if e == "pendente"), -1)
    
    segs = ""
    for k, e in enumerate(estados, start=1):
        cor = COR_TRECHO.get(e)
        if not cor:
            continue
        ini = 0 if k == 1 else (k - 1.5) / n * 100
        larg = (0.5 if k == 1 else 1) / n * 100 + 0.15
        raio = "10px 0 0 10px" if k == 1 else "0"
        segs += f'<div class="tl-seg" style="left:{ini:.3f}%;width:{larg:.3f}%;background:{cor};border-radius:{raio}"></div>'
    if estados[-1] == "ok":
        segs += f'<div class="tl-seg" style="left:{(n - 0.5) / n * 100:.3f}%;width:{0.5 / n * 100:.3f}%;background:{COR_TRECHO["ok"]};border-radius:0 10px 10px 0"></div>'
    
    itens = ""
    for i, (nome, e) in enumerate(zip(NOMES_FASES, estados)):
        is_cur = (i == atual_idx and e == "pendente")
        cls = "completed" if e == "ok" else "blocked" if e == "bloqueado" else "warn" if e == "nao_realizado" else "skipped" if e == "pulado" else ("cur" if is_cur else "")
        ic = "✓" if e == "ok" else "✕" if e == "bloqueado" else "!" if e == "nao_realizado" else "–" if e == "pulado" else ""
        
        tag = "Bloqueado" if e == "bloqueado" else ("Em andamento" if is_cur else ("Concluído" if e == "ok" else ""))
        if e == "nao_realizado":
            nome = f"{nome} não realizado"
            
        itens += f'<div class="tl-item {cls}"><div class="tl-c">{ic}</div><div class="tl-n">{nome}</div><div class="tl-t">{tag}</div></div>'
    return compacta(CSS_TL + f'<div class="tl-wrap">{segs}{itens}</div>')


CSS_CARDS = """<style>
.hb-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:8px}
@media(max-width:900px){.hb-grid{grid-template-columns:1fr}}
.hb-card{background:#fff;border-radius:18px;padding:14px 16px;box-shadow:0 3px 10px rgba(59,113,159,.10);border-top:5px solid #3b719f;text-align:left}
.hb-h{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px;font-weight:800;letter-spacing:.5px;text-transform:uppercase;color:#1f3a57;margin-bottom:8px}
.hb-h em{font-style:normal;font-size:11px;font-weight:700;padding:3px 8px;border-radius:20px;white-space:nowrap;text-transform:none;letter-spacing:0}
.hb-h em.ok{background:#e3f6e8;color:#1e8e3e}.hb-h em.bad{background:#fde2e2;color:#c62828}
.hb-h em.skip{background:#e8edf5;color:#6b7a90}.hb-h em.warn{background:#fff3d6;color:#b45309}.hb-h em.part{background:#fff3d6;color:#b45309}.hb-h em.pend{background:#eef2f8;color:#6b7a90}
.hb-row{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px dashed #e1e9f5;font-size:13px}
.hb-row:last-child{border-bottom:none}
.hb-row span{color:#6b7a90}.hb-row b{color:#3b719f;text-align:right;overflow-wrap:anywhere}.hb-row b.vz{color:#b3bdcc;font-weight:500}
.hb-chips{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin:6px 0 4px}
.hb-chip{background:#fff;border-radius:20px;padding:6px 14px;font-size:13px;color:#1f3a57;box-shadow:0 2px 6px rgba(59,113,159,.10)}
.hb-kpis{display:grid;grid-template-columns:repeat(7,1fr);gap:5px;margin:8px 0 16px;width:100%}
@media(max-width:1400px){.hb-kpis{grid-template-columns:repeat(4,1fr)}}
@media(max-width:900px){.hb-kpis{grid-template-columns:repeat(2,1fr)}}
.hb-kpi{background:#fff;border-radius:14px;padding:14px 4px;text-align:center;box-shadow:0 4px 14px rgba(59,113,159,.10);border-top:6px solid #3b719f;min-height:115px;display:flex;flex-direction:column;justify-content:center;box-sizing:border-box}
.hb-kpi .n{font-size:38px;font-weight:800;color:#3b719f;line-height:1.1}
.hb-kpi .l{font-size:10px;font-weight:700;color:#5c6b73;margin-top:6px;text-transform:uppercase;letter-spacing:.1px}
</style>"""

FASES_CARDS = [
    ("Registro de Clientes", "#5a5e6a", [0], [("ordem_status",), ("cliente",)]),
    ("Recebimento | Logística", "#6aa84f", [1], [("nf",), ("itens",), ("data_nf",), ("data_receb",)]),
    ("Recebimento | Serviços", "#e0a800", [2], [("data_crm",), ("cas",), ("service",)]),
    ("Verificações & Diagnósticos", "#5681ac", [3, 4], [("data_verif",), ("status_verif",), ("aprovacao",)]),
    ("Reparos & Manutenções", "#e69138", [5], [("aceite",), ("data_ordem",), ("data_reparo",)]),
    ("Devolução | Logística", "#6b7280", [6, 7], [("solic_devol",), ("status_devol",), ("status_concl",)]),
]


def _badge(estados, idx):
    s = [estados[i] for i in idx]
    if "bloqueado" in s:
        return "✕ Reprovado", "bad"
    if "nao_realizado" in s:
        return "⚠ Reparo não realizado", "warn"
    if "pendente" not in s:
        return ("– Não se aplica", "skip") if all(x == "pulado" for x in s) else ("✓ Concluída", "ok")
    if any(x == "ok" for x in s):
        return "◐ Em andamento", "part"
    return "⏳ Pendente", "pend"


def html_cards(r, estados):
    cartoes = ""
    for titulo, cor, idx, campos in FASES_CARDS:
        txt, cls = _badge(estados, idx)
        linhas = "".join(
            f'<div class="hb-row"><span>{LABEL[k]}</span><b class="{"" if r[k] else "vz"}">{esc(r[k]) if r[k] else "—"}</b></div>'
            for (k,) in campos)
        cartoes += (f'<div class="hb-card" style="border-top-color:{cor}"><div class="hb-h"><span>{titulo}</span>'
                    f'<em class="{cls}">{txt}</em></div>{linhas}</div>')
    return compacta(CSS_CARDS + f'<div class="hb-grid">{cartoes}</div>')


def html_kpis(df):
    abertos = df[~df["concluido"]]
    cont = abertos["grupo"].value_counts()
    
    card_ordem = [
        ("A fazer", COR_GRUPO["A fazer"]),
        ("Follow up", COR_GRUPO["Follow up"]),
        ("Importação", COR_GRUPO["Importação"]),
        ("Garantia", COR_GRUPO["Garantia"]),
        ("Reprovado", COR_GRUPO["Reprovado"]),
        ("Aprovado", COR_GRUPO["Aprovado"]),
        ("Finalizados", COR_GRUPO["Finalizados"])
    ]
    
    cards = ""
    for g, cor in card_ordem:
        if g == "Finalizados":
            val = int(df["concluido"].sum())
        else:
            val = int(cont.get(g, 0))
        cards += f'<div class="hb-kpi" style="border-top-color:{cor}"><div class="n">{val}</div><div class="l">{g}</div></div>'
    return compacta(CSS_CARDS + f'<div class="hb-kpis">{cards}</div>')


_CSS_TAB = """
body{margin:0;background:transparent;font-family:"Source Sans Pro",Arial,sans-serif;color:#1f3a57}
.top{display:flex;justify-content:space-between;align-items:center;padding:2px 4px 8px;font-size:13px;color:#6b7a90}
.top button{border:1px solid #3b719f;background:#fff;color:#3b719f;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px}
.top button:hover{background:#3b719f;color:#fff}
.tb{max-height:600px;overflow:auto;border-radius:8px;background:#fff;box-shadow:0 2px 6px rgba(0,0,0,.08)}
table{width:100%;border-collapse:collapse;font-size:14px}
.hd th{position:sticky;top:0;height:41px;box-sizing:border-box;background:#3b719f;color:#fff;padding:0 8px;z-index:2;cursor:pointer;user-select:none;white-space:nowrap}
.hd th:hover{background:#2c5a82}
.ft td{position:sticky;top:41px;background:#e6eef6;padding:5px 6px;z-index:2;border-bottom:1px solid #d5e1f2}
.ft input,.ft select{width:100%;box-sizing:border-box;padding:4px 6px;border:1px solid #c5d3e8;border-radius:5px;font-size:12px;background:#fff;color:#1f3a57}
tbody td{padding:8px;border-bottom:1px solid #e4e9f0;text-align:center}
"""
_JS_TAB = """
const tb=document.querySelector('#t tbody');let rows=[...tb.rows];
const ths=[...document.querySelectorAll('.hd th')];
const fls=[...document.querySelectorAll('.ft [data-f]')];
const cnt=document.getElementById('cnt');let col=-1,dir=1;
function marcar(){ths.forEach((h,i)=>h.querySelector('i').textContent=i===col?(dir>0?' ▲':' ▼'):'');}
function filtrar(){let n=0;rows.forEach(r=>{let ok=true;fls.forEach((el,i)=>{const v=el.value.toLowerCase();if(!v)return;
 const txt=r.cells[i].textContent.trim().toLowerCase();
 if(el.tagName==='SELECT'?txt!==v:txt.indexOf(v)<0)ok=false;});r.style.display=ok?'':'none';if(ok)n++;});
 cnt.textContent=n+' de '+rows.length+' caso(s)';}
function ordenar(i){dir=(col===i)?-dir:1;col=i;const num=ths[i].dataset.t!=='t';
 rows.sort((a,b)=>{let x=a.cells[i].dataset.v,y=b.cells[i].dataset.v;
  if(num){x=parseFloat(x);y=parseFloat(y);const nx=isNaN(x),ny=isNaN(y);if(nx&&ny)return 0;if(nx)return 1;if(ny)return -1;return (x-y)*dir;}
  return x.localeCompare(y,'pt-BR')*dir;});
 rows.forEach(r=>tb.appendChild(r));marcar();}
ths.forEach((h,i)=>h.addEventListener('click',()=>ordenar(i)));
fls.forEach(el=>{el.addEventListener('input',filtrar);el.addEventListener('change',filtrar);});
document.getElementById('lp').addEventListener('click',()=>{fls.forEach(el=>el.value='');filtrar();});
col=__DIAS__;dir=-1;marcar();filtrar();
"""


def html_tabela_interativa(df):
    cols = [("Cliente", "cliente", "t"), ("NF", "nf", "t"), ("CAS-SO-BR", "cas", "t"), ("Recebimento", "data_receb", "d"),
            ("Dias", "dias", "n"), ("Status", "status", "t"), ("Status de Conclusão", "status_concl", "t")]
    regs = df.to_dict("records")

    def texto(r, k):
        v = r[k]
        if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
            return "-"
        return f"{int(v)}" if k == "dias" else str(v)

    def chave(r, k, tipo):
        if tipo == "n":
            return "" if pd.isna(r[k]) else str(int(r[k]))
        if tipo == "d":
            d = parse_data(r[k])
            return "" if pd.isna(d) else str(d.toordinal())
        return _norm(texto(r, k))

    th = "".join(f'<th data-t="{t}">{n}<i></i></th>' for n, _, t in cols)
    fl = ""
    for n, k, t in cols:
        vals = sorted({texto(r, k) for r in regs}, key=_norm)
        if t == "t" and len(vals) <= 25:
            ops = '<option value="">Todos</option>' + "".join(f'<option value="{esc(v.lower())}">{esc(v)}</option>' for v in vals)
            fl += f'<td><select data-f>{ops}</select></td>'
        else:
            fl += '<td><input data-f type="text" placeholder="Filtrar..."></td>'
    linhas = ""
    for r in regs:
        d = r["dias"]
        cor = ("#fde2e2" if d >= ALERTA_VERMELHO_DIAS else "#fff3d6" if d >= ALERTA_AMARELO_DIAS else "") if pd.notna(d) else ""
        tds = "".join(f'<td data-v="{esc(chave(r, k, t))}">{esc(texto(r, k))}</td>' for _, k, t in cols)
        linhas += f'<tr style="background:{cor}">{tds}</tr>'
    js = _JS_TAB.replace("__DIAS__", str([c[1] for c in cols].index("dias")))
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_CSS_TAB}</style></head><body>'
            f'<div class="top"><span id="cnt"></span><button id="lp">Limpar filtros</button></div>'
            f'<div class="tb"><table id="t"><thead><tr class="hd">{th}</tr><tr class="ft">{fl}</tr></thead>'
            f'<tbody>{linhas}</tbody></table></div><script>{js}</script></body></html>')


def card_aviso(titulo, sub):
    return compacta(f"""<div style="background:#fff4e5;border:2px solid #f5a623;border-radius:12px;padding:24px 20px;text-align:center;
        box-shadow:0 4px 12px rgba(245,166,35,.18);margin-top:10px"><div style="font-size:34px;line-height:1">⚠️</div>
        <div style="font-size:22px;font-weight:800;color:#b45309;margin-top:8px">{titulo}</div>
        <div style="font-size:15px;color:#5c6b73;margin-top:8px">{sub}</div></div>""")


_uid = count(1)
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
CSS_DASH = """<style>
.pf-grid{display:grid;gap:16px;margin-bottom:16px}
.pf-r1{grid-template-columns:repeat(4,1fr)}
.pf-r2{grid-template-columns:270px 1fr}
.pf-r3{grid-template-columns:1.2fr 1fr 1fr}
@media(max-width:1000px){.pf-r1,.pf-r2,.pf-r3{grid-template-columns:1fr}}
.pf-card{background:#fff;border-radius:20px;padding:18px 20px;box-shadow:0 3px 10px rgba(59,113,159,.10);text-align:left;overflow:hidden}
.pf-t{font-size:12px;font-weight:700;letter-spacing:.5px;color:#5c6b83;text-transform:uppercase}
.pf-s{font-size:12px;color:#8a97ab;margin-top:2px}
.pf-v{font-size:36px;font-weight:700;color:#3b719f;line-height:1.15;margin-top:6px}
.pf-blue{background:linear-gradient(90deg,#6d8fb6,#3b719f);color:#fff;display:flex;flex-direction:column;justify-content:center}
.pf-blue .pf-t,.pf-blue .pf-s{color:#e3edfb}
.pf-duo{display:flex;justify-content:space-around;gap:10px;margin-top:8px}
.pf-duo div{text-align:center}
.pf-duo b{display:block;font-size:38px;line-height:1.1}
.pf-duo span{font-size:11px;color:#e3edfb}
.pf-don{display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center}
.pf-don+.pf-don{margin-top:18px;padding-top:16px;border-top:1px solid #e1e9f5}
.pf-big{display:flex;gap:18px;align-items:center}
.pf-big .side{min-width:150px;text-align:left}
.pf-big .side b{display:block;font-size:30px;color:#3b719f;line-height:1.1}
.pf-big .side span{font-size:12px;color:#8a97ab;display:block;margin-bottom:14px}
.pf-li{margin-top:14px}
.pf-lr{display:flex;justify-content:space-between;gap:8px;font-size:13px;font-weight:600;color:#1f3a57}
.pf-lr span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pf-lr b{color:#3b719f;white-space:nowrap}
.pf-tr{height:6px;border-radius:6px;background:#e1e9f5;margin-top:5px}
.pf-fi{height:6px;border-radius:6px;background:linear-gradient(90deg,#6d8fb6,#3b719f)}
</style>"""


def _nome_mes(p):
    return f"{MESES[p.month - 1]}/{p.year}"


def _fmt1(x):
    return f"{x:.1f}".replace(".", ",")


def _spark(vals, w=240, h=46):
    vals = [float(v) for v in vals]
    u = next(_uid)
    if len(vals) < 2:
        return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}"><line x1="2" y1="{h-6}" x2="{w-2}" '
                f'y2="{h-6}" stroke="#dbe5f3" stroke-width="2" stroke-linecap="round"/></svg>')
    mn, mx = min(vals), max(vals)
    rng = (mx - mn) or 1
    pts = [(2 + i * (w - 4) / (len(vals) - 1), h - 8 - (v - mn) / rng * (h - 16)) for i, v in enumerate(vals)]
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none">'
            f'<defs><linearGradient id="sp{u}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6d8fb6" '
            f'stop-opacity=".25"/><stop offset="1" stop-color="#6d8fb6" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="2,{h} {d} {w-2},{h}" fill="url(#sp{u})"/>'
            f'<polyline points="{d}" fill="none" stroke="#6d8fb6" stroke-width="2.2" stroke-linejoin="round" '
            f'stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>')


def _donut(pct, legenda):
    pct = max(0.0, min(100.0, pct))
    r = 42
    circ = 2 * 3.14159265 * r
    u = next(_uid)
    return (f'<div class="pf-don"><svg viewBox="0 0 110 110" width="120" height="120">'
            f'<defs><linearGradient id="dn{u}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6d8fb6"/>'
            f'<stop offset="1" stop-color="#3b719f"/></linearGradient></defs>'
            f'<circle cx="55" cy="55" r="{r}" fill="none" stroke="#dbe5f3" stroke-width="9"/>'
            f'<circle cx="55" cy="55" r="{r}" fill="none" stroke="url(#dn{u})" stroke-width="9" stroke-linecap="round" '
            f'stroke-dasharray="{circ * pct / 100:.1f} {circ:.1f}" transform="rotate(-90 55 55)"/>'
            f'<text x="55" y="62" text-anchor="middle" font-size="22" font-weight="700" fill="#3b719f">{pct:.0f}%</text>'
            f'</svg><div class="pf-s">{legenda}</div></div>')


def _linha(rot, vals, w=640, h=230):
    n = len(vals)
    pl, pr, pt, pb = 20, 20, 26, 34
    mx = max(max(vals), 1)
    X = lambda i: pl + (w - pl - pr) * (i / (n - 1) if n > 1 else 0.5)
    Y = lambda v: pt + (h - pt - pb) * (1 - v / mx)
    grade = "".join(f'<line x1="{pl}" y1="{pt + (h - pt - pb) * k / 3:.1f}" x2="{w - pr}" '
                    f'y2="{pt + (h - pt - pb) * k / 3:.1f}" stroke="#e1e9f5"/>' for k in range(4))
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    bolas = "".join(
        f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="5" fill="#fff" stroke="#6d8fb6" stroke-width="3"/>'
        f'<text x="{X(i):.1f}" y="{Y(v) - 11:.1f}" text-anchor="middle" font-size="11" font-weight="600" '
        f'fill="#3b719f">{v}</text>' for i, v in enumerate(vals))
    eixo = "".join(f'<text x="{X(i):.1f}" y="{h - 10}" text-anchor="middle" font-size="11" fill="#8a97ab">{r}</text>'
                   for i, r in enumerate(rot))
    return (f'<svg viewBox="0 0 {w} {h}" width="100%">{grade}<polyline points="{pts}" fill="none" stroke="#6d8fb6" '
            f'stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>{bolas}{eixo}</svg>')


def _barras(rot, vals, w=360, h=210, fmt=str):
    n = len(vals)
    mx = max(max(vals), 1)
    slot = w / n
    bw = min(34, slot * 0.55)
    top, bot = 26, h - 34
    u = next(_uid)
    corpo = ""
    for i, (r, v) in enumerate(zip(rot, vals)):
        x = slot * i + (slot - bw) / 2
        fh = (bot - top) * v / mx
        corpo += (f'<rect x="{x:.1f}" y="{top}" width="{bw:.1f}" height="{bot - top}" rx="{bw / 2:.1f}" fill="#e7edf6"/>'
                  + (f'<rect x="{x:.1f}" y="{bot - fh:.1f}" width="{bw:.1f}" height="{fh:.1f}" rx="{bw / 2:.1f}" '
                     f'fill="url(#br{u})"/>' if v > 0 else "")
                  + f'<text x="{x + bw / 2:.1f}" y="{top - 8}" text-anchor="middle" font-size="12" font-weight="700" '
                    f'fill="#3b719f">{fmt(v)}</text>'
                  + f'<text x="{x + bw / 2:.1f}" y="{h - 12}" text-anchor="middle" font-size="11" fill="#8a97ab">{r}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%"><defs><linearGradient id="br{u}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="#6d8fb6"/><stop offset="1" stop-color="#3b719f"/></linearGradient></defs>'
            f'{corpo}</svg>')


def _lista(itens, fmt):
    if not itens:
        return '<div class="pf-s" style="margin-top:14px">Sem dados para exibir.</div>'
    mx = max(v for _, v in itens) or 1
    return "".join(
        f'<div class="pf-li"><div class="pf-lr"><span>{esc(str(n))}</span><b>{fmt(v)}</b></div>'
        f'<div class="pf-tr"><div class="pf-fi" style="width:{max(v / mx * 100, 4):.0f}%"></div></div></div>'
        for n, v in itens)


# ======================= GESTÃO (DASHBOARD) =======================
CLIENTES_IGNORADOS_GESTAO = []


def preparar_gestao(casos):
    g = casos.copy()
    if CLIENTES_IGNORADOS_GESTAO:
        nomes = g["cliente"].map(_norm)
        g = g[~nomes.apply(lambda n: any(i in n for i in CLIENTES_IGNORADOS_GESTAO))].copy()
    for k in ("data_receb", "data_crm", "data_verif", "data_ordem", "data_reparo"):
        g["d_" + k] = pd.to_datetime(g[k].map(parse_data), errors="coerce")
    g["ap"] = g["aprovacao"].map(_norm)

    def dif(a, b):
        d = (g["d_" + b] - g["d_" + a]).dt.days
        return d.where((d >= 0) & (d <= 730))

    g["t_log_crm"] = dif("data_receb", "data_crm")
    g["t_verif"] = dif("data_crm", "data_verif")
    g["t_pedido"] = dif("data_verif", "data_ordem")
    g["t_reparo"] = dif("data_ordem", "data_reparo")
    g["t_total"] = dif("data_receb", "data_reparo").where(~g["ap"].isin(APROVACOES_REPROVADAS))
    return g


def montar_gestao(g, sel, hoje):
    abertos = g[~g["concluido"].astype(bool)]
    total = len(g)
    criticos = int((abertos["dias"] >= ALERTA_VERMELHO_DIAS).sum())

    tt = g.dropna(subset=["t_total"])
    media = tt["t_total"].mean() if len(tt) else None
    serie = tt.groupby(tt["d_data_reparo"].dt.to_period("M"))["t_total"].mean().tail(8).tolist()
    k1 = (f'<div class="pf-card"><div class="pf-t">Lead time médio</div>'
          f'<div class="pf-v">{_fmt1(media) + " dias" if media is not None else "—"}</div>'
          f'<div class="pf-s">Do recebimento ao reparo finalizado (exceto reprovados) · {len(tt)} casos</div>{_spark(serie)}</div>')

    rec = g.dropna(subset=["d_data_receb"]).copy()
    rec["mes"] = rec["d_data_receb"].dt.to_period("M")
    if sel is not None:
        mes = rec[rec["mes"] == sel]
        ult = hoje.day if sel == hoje.to_period("M") else sel.days_in_month
        diario = mes.groupby(mes["d_data_receb"].dt.day).size().reindex(range(1, ult + 1), fill_value=0).tolist()
        qtd, rot = len(mes), _nome_mes(sel)
    else:
        diario, qtd, rot = [], 0, "—"
    k2 = (f'<div class="pf-card"><div class="pf-t">Recebidos no mês</div><div class="pf-v">{qtd}</div>'
          f'<div class="pf-s">{rot} · equipamentos por dia</div>{_spark(diario)}</div>')

    aprov = g["ap"].isin(APROVACOES_OK).sum()
    reprov = g["ap"].isin(APROVACOES_REPROVADAS).sum()
    decididos = int(aprov + reprov)
    taxa = reprov / decididos * 100 if decididos else 0
    k3 = (f'<div class="pf-card"><div class="pf-t">Taxa de reprovação</div><div class="pf-v">{taxa:.0f}%</div>'
          f'<div class="pf-s" style="font-weight:600;color:#1f3a57">{int(reprov)} reprovados de {decididos} relatórios decididos</div>'
          f'<div class="pf-s">Equipamentos devolvidos sem reparo</div></div>')

    k4 = (f'<div class="pf-card pf-blue"><div class="pf-t">Em andamento</div>'
          f'<div class="pf-duo"><div><b>{len(abertos)}</b><span>Casos abertos</span></div>'
          f'<div><b>{criticos}</b><span>Críticos (≥ {ALERTA_VERMELHO_DIAS} dias)</span></div></div></div>')

    fin = total - len(abertos)
    donuts = (_donut(fin / total * 100 if total else 0, f"Casos finalizados ({fin} de {total})")
              + _donut(criticos / len(abertos) * 100 if len(abertos) else 0, "Em andamento há mais de 60 dias"))

    if sel is not None:
        per = pd.period_range(end=sel, periods=12, freq="M")
        cont = rec.groupby("mes").size().reindex(per, fill_value=0)
        linha = _linha([f"{MESES[p.month - 1]}/{str(p.year)[2:]}" for p in per], cont.tolist())
        media_m = cont.mean()
    else:
        linha, media_m = "", 0
    grande = (f'<div class="pf-card"><div class="pf-t">Equipamentos recebidos · últimos 12 meses</div>'
              f'<div class="pf-big"><div style="flex:1;min-width:0">{linha}</div><div class="side">'
              f'<span>Mês selecionado</span><b>{qtd}</b>'
              f'<span style="margin-top:14px">Média mensal</span><b>{_fmt1(media_m)}</b></div></div></div>')

    fases = [("Logística → CRM", "t_log_crm"), ("Verificação", "t_verif"), ("Aprov. → Pedido", "t_pedido"), ("Reparo", "t_reparo")]
    medias = [round(float(g[c].mean()), 1) if g[c].notna().any() else 0 for _, c in fases]
    barras_fase = (f'<div class="pf-card"><div class="pf-t">Lead time por fase</div>'
                   f'<div class="pf-s">Média de dias em cada etapa (casos com as duas datas preenchidas)</div>'
                   f'{_barras([n for n, _ in fases], medias, fmt=_fmt1)}</div>')

    sit = abertos["grupo"].value_counts()
    sit_l = [(gp, int(sit[gp])) for gp in ["A fazer", "Follow up", "Importação", "Garantia", "Reprovado", "Aprovado"] if sit.get(gp, 0)]
    l_sit = (f'<div class="pf-card"><div class="pf-t">Situação dos casos em andamento</div>'
             f'<div class="pf-s">Onde cada equipamento está hoje no fluxo</div>{_lista(sit_l, lambda v: f"{v}")}</div>')

    nomes_ap = {"garantia": "Garantia", "aprovado": "Aprovado", "cortesia": "Cortesia", "reprovado": "Reprovado"}
    res = [(n, int((g["ap"] == k).sum())) for k, n in nomes_ap.items()]
    espera = int(((g["ap"] == "") & g["d_data_verif"].notna() & ~g["concluido"].astype(bool)).sum())
    res.append(("Aguardando decisão", espera))
    res = sorted([x for x in res if x[1]], key=lambda x: -x[1])
    l_res = (f'<div class="pf-card"><div class="pf-t">Resultado dos relatórios</div>'
             f'<div class="pf-s">Decisão registrada em "Aprovação de relatório"</div>{_lista(res, lambda v: f"{v}")}</div>')

    ordem = ["até 30 dias", "31–60", "61–90", "91–180", "+180"]
    fx = pd.cut(abertos["dias"].dropna(), [-1, 30, 60, 90, 180, 10**6], labels=ordem).astype(str).value_counts().reindex(ordem, fill_value=0)
    barras_idade = (f'<div class="pf-card"><div class="pf-t">Idade dos casos em andamento</div>'
                    f'<div class="pf-s">Dias desde o recebimento do equipamento</div>{_barras(ordem, fx.tolist())}</div>')

    aberto_c = abertos.dropna(subset=["dias"]).groupby("cliente")["dias"].mean().sort_values(ascending=False).head(5)
    l_cli = (f'<div class="pf-card"><div class="pf-t">Clientes com maior tempo em aberto</div>'
             f'<div class="pf-s">Média de dias dos casos ainda em andamento</div>'
             f'{_lista([(c, v) for c, v in aberto_c.items() if c], lambda v: f"{v:.0f} dias")}</div>')
    vol = g[g["cliente"] != ""].groupby("cliente").size().sort_values(ascending=False).head(5)
    l_vol = (f'<div class="pf-card"><div class="pf-t">Clientes com mais equipamentos</div>'
             f'<div class="pf-s">Quantidade de casos no histórico completo</div>'
             f'{_lista([(c, int(v)) for c, v in vol.items()], lambda v: f"{v}")}</div>')

    h = (CSS_DASH + f'<div class="pf-grid pf-r1">{k1}{k2}{k3}{k4}</div>'
         f'<div class="pf-grid pf-r2"><div class="pf-card">{donuts}</div>{grande}</div>'
         f'<div class="pf-grid pf-r3">{barras_fase}{l_sit}{l_res}</div>'
         f'<div class="pf-grid pf-r3">{barras_idade}{l_cli}{l_vol}</div>')
    return compacta(h)


# ======================= CARREGAMENTO DE DADOS =======================
hoje = pd.Timestamp.today().normalize()
try:
    casos, ausentes = ler_casos(ler_valores(), hoje)
except Exception as e:
    st.error(f"Erro ao conectar com a planilha: {e}")
    st.stop()


# ======================= NAVEGAÇÃO LATERAL (SIDEBAR) =======================
chaves = ["gestao", "consulta", "casos", "gerenciar"]
nomes_completos = ["📊 Dashboard", "🔎 Consulta", "📋 Casos em andamento", "✏️ Inserir / Atualizar Case"]
icones_apenas = ["📊", "🔎", "📋", "✏️"]

if "pagina_conserto" not in st.session_state:
    st.session_state["pagina_conserto"] = "gestao"

with st.sidebar:
    if expandido:
        c_logo, c_btn = st.columns([4, 1])
        with c_logo:
            st.markdown('<div class="sb-header" style="border:none;margin:0;padding:0;"><div class="sb-logo">🛠️<span>HUB DE<br>CONSERTO</span></div></div>', unsafe_allow_html=True)
        with c_btn:
            if st.button("◀", key="toggle_sb", use_container_width=True):
                st.session_state["sidebar_expanded"] = False
                st.rerun()
    else:
        st.markdown('<div style="text-align:center;font-size:24px;margin-bottom:10px;">🛠️</div>', unsafe_allow_html=True)
        if st.button("▶", key="toggle_sb", use_container_width=True):
            st.session_state["sidebar_expanded"] = True
            st.rerun()

    st.markdown('<div style="border-bottom:1px solid rgba(255,255,255,.22);margin-bottom:16px;"></div>', unsafe_allow_html=True)

    rotulos = nomes_completos if expandido else icones_apenas
    for _k, _label in zip(chaves, rotulos):
        _ativo = st.session_state["pagina_conserto"] == _k
        if st.button(_label, key=f"nav_{_k}", use_container_width=True, type="primary" if _ativo else "secondary"):
            st.session_state["pagina_conserto"] = _k
            st.rerun()

    st.markdown('<div style="border-bottom:1px solid rgba(255,255,255,.22);margin:16px 0;"></div>', unsafe_allow_html=True)

    # 🔄 BOTÃO DE ATUALIZAÇÃO MANUAL
    if st.button("🔄 Atualizar Dados", use_container_width=True, help="Limpa o cache e busca os dados mais recentes do Google Sheets"):
        st.cache_data.clear()
        st.rerun()

    texto_conexao = "🟢 Conexão Estabelecida" if expandido else "🟢"
    st.markdown(
        f'<div class="sidebar-status-container">{texto_conexao}</div>',
        unsafe_allow_html=True
    )

pagina = st.session_state["pagina_conserto"]


# ======================= CABEÇALHO DA PÁGINA =======================
PAGINAS = {
    "gestao": ("Visão geral.", "Lead time, volume e gargalos do conserto, em tempo real."),
    "consulta": ("Onde está o equipamento.", "Digite a NF, o CAS ou a Service Notification e veja o fluxo completo."),
    "casos": ("Em andamento.", "Todos os casos abertos, dos mais antigos aos mais recentes."),
    "gerenciar": ("Inserir ou atualizar.", "O que você registra aqui vai direto para a planilha."),
}
_tit, _sub = PAGINAS.get(pagina, ("Hub de Conserto.", ""))
st.markdown(f"""
    <div class="header-banner">
        <div class="hb-kicker">HUB DE CONSERTO</div>
        <h1>{esc(_tit)}</h1>
        <p>{esc(_sub)}</p>
    </div>
""", unsafe_allow_html=True)

if ausentes:
    st.warning("Não encontrei no cabeçalho da planilha as colunas: " + ", ".join(ausentes)
               + ". Confira se os nomes não foram alterados.")


# ======================= CONTEÚDO DAS PÁGINAS =======================

# -------- 1. CONSULTA --------
if pagina == "consulta":
    _, mid, _ = st.columns([1, 6, 1])
    with mid:
        st.write("Digite o nº da Nota Fiscal, CAS-SO-BR, Service Notification ou parte do nome do cliente.")
        q = st.text_input("Pesquisar:", placeholder="Ex: 2655, 204607, 62500447 ou NCC", key="busca_hub").strip()
        if q:
            qn = _norm(q)
            achou = casos[(casos["nf"].map(_norm) == qn) | (casos["cas"].map(_norm) == qn)
                          | (casos["service"].map(_norm) == qn)]
            if achou.empty and len(qn) >= 3:
                achou = casos[casos["cliente"].map(_norm).str.contains(qn, regex=False)]
            if achou.empty:
                st.markdown(card_aviso("Equipamento não encontrado", f"Nenhum registro para: <b>{esc(q)}</b>. Confira o número digitado."),
                            unsafe_allow_html=True)
            else:
                if len(achou) > 1:
                    op = {f"NF {r['nf'] or '-'} — {r['cliente']} — CAS {r['cas'] or '-'}": i for i, r in achou.iterrows()}
                    r = achou.loc[op[st.selectbox("Mais de um registro encontrado, escolha:", list(op))]]
                else:
                    r = achou.iloc[0]
                av = r["_aval"]
                st.markdown(f"## 📋 Cliente: {r['cliente']}")
                st.markdown(f"### Status Atual: {av['status']}")
                (st.error if av["grupo"] == "Reprovado" else st.info)(av["detalhe"])
                if av["faltam"]:
                    st.warning("Falta preencher na planilha: " + " · ".join(av["faltam"]))
                dias = f"{int(r['dias'])} dia(s)" if pd.notna(r["dias"]) else "—"
                
                st.markdown(compacta(CSS_CARDS + f'<div class="hb-chips"><span class="hb-chip">📅 Data Hub <b>{esc(r["data_receb"] or "—")}</b></span>'
                                     f'<span class="hb-chip">⏱️ Em atendimento há <b>{dias}</b></span></div>'), unsafe_allow_html=True)
                st.markdown("### ⏳ Fluxo do Conserto:")
                st.markdown(html_timeline(av["estados"]), unsafe_allow_html=True)
                st.markdown("---")
                st.markdown("### 🔍 Detalhes por etapa:")
                st.markdown(html_cards(r, av["estados"]), unsafe_allow_html=True)

# -------- 2. CASOS EM ANDAMENTO --------
elif pagina == "casos":
    st.markdown(html_kpis(casos), unsafe_allow_html=True)
    inc = st.toggle("Incluir casos finalizados")
    v = casos if inc else casos[~casos["concluido"]]
    st.caption(f"Clique no título de uma coluna para ordenar · use os campos abaixo dos títulos para filtrar · "
               f"amarelo ≥ {ALERTA_AMARELO_DIAS} dias · vermelho ≥ {ALERTA_VERMELHO_DIAS} dias desde o recebimento")
    v = v.sort_values("dias", ascending=False, na_position="last")
    components.html(html_tabela_interativa(v), height=690, scrolling=False)

# -------- DASHBOARD (página "gestao") --------
elif pagina == "gestao":
    gest = preparar_gestao(casos)
    meses = sorted(gest["d_data_receb"].dropna().dt.to_period("M").unique(), reverse=True)
    sel = None
    if meses:
        atual = hoje.to_period("M")
        _, cs, _ = st.columns([2, 1, 2])
        sel = cs.selectbox("Mês de referência (recebimentos)", meses,
                           index=meses.index(atual) if atual in meses else 0, format_func=_nome_mes, key="mes_gestao")
    st.markdown(montar_gestao(gest, sel, hoje), unsafe_allow_html=True)

# -------- 4. INSERIR / ATUALIZAR CASE --------
elif pagina == "gerenciar":
    st.subheader("✏️ Inserir Novo Case ou Atualizar Case Existente")
    
    modo = st.radio("Selecione a operação:", ["➕ Inserir Novo Case", "✏️ Atualizar Case Existente"], horizontal=True)
    
    caso_existente = None
    row_num = None
    
    if modo == "✏️ Atualizar Case Existente":
        pesquisa_id = st.text_input("Digite o nº da Nota Fiscal ou Service Notification para carregar o case:", placeholder="Ex: 828848 ou 62500507").strip()
        if pesquisa_id:
            match = casos[(casos["nf"].str.strip() == pesquisa_id) | (casos["service"].str.strip() == pesquisa_id)]
            if not match.empty:
                caso_existente = match.iloc[0]
                row_num = int(caso_existente["_row"])
                st.success(f"Case encontrado! Cliente: **{caso_existente['cliente']}** (NF: {caso_existente['nf']} | Service: {caso_existente['service']})")
            else:
                st.warning("Nenhum case encontrado com esse número de NF ou Service Notification.")

    with st.form("form_case"):
        st.markdown('<div class="form-title">📁 Bloco 1: Registro de Clientes & Logística</div>', unsafe_allow_html=True)
        
        c1, c2, c3 = st.columns(3)
        with c1:
            ordem_status = st.text_input("Ordem Status", value=caso_existente["ordem_status"] if caso_existente is not None else "")
            cliente = st.text_input("Nome do cliente *", value=caso_existente["cliente"] if caso_existente is not None else "")
            nf = st.text_input("Nº da Nota Fiscal *", value=caso_existente["nf"] if caso_existente is not None else "")
        with c2:
            itens = st.text_input("Itens da nota", value=caso_existente["itens"] if caso_existente is not None else "")
            data_nf = st.text_input("Data da NF de origem", value=caso_existente["data_nf"] if caso_existente is not None else "")
            data_receb = st.text_input("Data de recebimento", value=caso_existente["data_receb"] if caso_existente is not None else "")
        with c3:
            data_crm = st.text_input("Data de Registro (CRM)", value=caso_existente["data_crm"] if caso_existente is not None else "")
            cas = st.text_input("CAS-SO-BR (CRM)", value=caso_existente["cas"] if caso_existente is not None else "")
            service = st.text_input("Service Notification", value=caso_existente["service"] if caso_existente is not None else "")
        
        st.markdown('<div class="form-title">🔍 Bloco 2: Verificação, Aprovação & Reparo</div>', unsafe_allow_html=True)
        
        c4, c5, c6 = st.columns(3)
        with c4:
            data_verif = st.text_input("Data final da verificação", value=caso_existente["data_verif"] if caso_existente is not None else "")
            status_verif = st.text_input("Status de Verificação", value=caso_existente["status_verif"] if caso_existente is not None else "")
        with c5:
            opcoes_aprov = ["", "Aprovado", "Cortesia", "Garantia", "Reprovado"]
            idx_aprov = opcoes_aprov.index(caso_existente["aprovacao"]) if caso_existente is not None and caso_existente["aprovacao"] in opcoes_aprov else 0
            aprovacao = st.selectbox("Aprovação de relatório", opcoes_aprov, index=idx_aprov)
            aceite = st.text_input("Aceite de Pedido (Ordem SAP)", value=caso_existente["aceite"] if caso_existente is not None else "")
        with c6:
            data_ordem = st.text_input("Data da Ordem do Pedido", value=caso_existente["data_ordem"] if caso_existente is not None else "")
            data_reparo = st.text_input("Data Reparo Finalizado", value=caso_existente["data_reparo"] if caso_existente is not None else "")
        
        st.markdown('<div class="form-title">🚚 Bloco 3: Devolução & Conclusão</div>', unsafe_allow_html=True)
        
        c7, c8 = st.columns(2)
        with c7:
            solic_devol = st.text_input("Solicitação de devolução", value=caso_existente["solic_devol"] if caso_existente is not None else "")
            status_devol = st.text_input("Status de devolução", value=caso_existente["status_devol"] if caso_existente is not None else "")
        with c8:
            status_concl = st.text_input("Status de conclusão", value=caso_existente["status_concl"] if caso_existente is not None else "")
        
        submitted = st.form_submit_button("💾 Salvar Dados na Planilha", use_container_width=True)

        if submitted:
            if not cliente.strip() or not nf.strip():
                st.error("Preencha ao menos o **Nome do cliente** e o **Nº da Nota Fiscal** para salvar.")
            else:
                dados_form = {
                    "ordem_status": ordem_status, "cliente": cliente, "nf": nf, "itens": itens,
                    "data_nf": data_nf, "data_receb": data_receb, "data_crm": data_crm, "cas": cas,
                    "service": service, "data_verif": data_verif, "status_verif": status_verif,
                    "aprovacao": aprovacao, "aceite": aceite, "data_ordem": data_ordem,
                    "data_reparo": data_reparo, "solic_devol": solic_devol, "status_devol": status_devol,
                    "status_concl": status_concl
                }
                
                if modo == "✏️ Atualizar Case Existente":
                    if row_num:
                        sucesso, msg = salvar_caso_planilha(dados_form, row_num=row_num)
                        if sucesso:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                    else:
                        st.error("Nenhum case foi selecionado para atualização. Busque por uma NF válida acima.")
                else:
                    sucesso, msg = salvar_caso_planilha(dados_form, row_num=None)
                    if sucesso:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

st.markdown("<br><hr><div style='text-align: center; color: #6b7a90; font-size: 13px;'>Hub de Conserto · Desenvolvido por Diego Elvis</div>", unsafe_allow_html=True)
