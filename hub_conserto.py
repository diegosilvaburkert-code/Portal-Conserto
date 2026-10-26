import json
import re
import unicodedata
from html import escape as esc

import gspread
import pandas as pd
import streamlit as st

# ======================= CONFIGURAÇÃO =======================
LINK_PLANILHA = "COLE_AQUI_O_LINK_DA_PLANILHA_DE_CONSERTO"
NOME_DA_ABA = "Lead Time"

# Regras do fluxo (ajuste aqui se a regra do seu processo for diferente)
APROVACOES_OK = {"aprovado", "cortesia", "garantia"}      # relatório aprovado -> segue para reparo
APROVACOES_REPROVADAS = {"reprovado"}                      # relatório reprovado -> sem reparo, vai para devolução
APROVACOES_SEM_PEDIDO = {"cortesia", "garantia"}           # não exigem Aceite de Pedido (Ordem SAP)
CONCLUIDOS = {"concluido", "finalizado"}                   # valores de "Status de conclusão" que encerram o caso
ALERTA_AMARELO_DIAS = 30                                   # dias desde o recebimento
ALERTA_VERMELHO_DIAS = 60

st.set_page_config(page_title="Hub de Conserto", page_icon="🛠️", layout="wide")

st.markdown("""
    <style>
        .stApp { background-color: #e6effa; }
        .stApp h2, .stApp h3, .stApp h4, .stApp p, .stMarkdown p { text-align: center !important; }
        .stAlert { border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); text-align: center !important; }
        .stAlert > div { justify-content: center !important; text-align: center !important; }
        [data-testid="stWidgetLabel"] { text-align: center !important; display: block !important; width: 100% !important; }
        [data-testid="stTextInput"] div[data-baseweb="input"] { max-width: 560px !important; margin: 0 auto !important; }
        [data-testid="stTextInput"] input { text-align: center !important; }
        div[data-baseweb="tab-list"], [data-testid="stTabs"] [role="tablist"] { justify-content: center !important; }
        .header-banner { background: linear-gradient(90deg, #4f83cc, #1b4f93); padding: 25px; border-radius: 10px;
            color: white; text-align: center; margin-bottom: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }
        .header-banner h1 { color: white !important; font-size: 24px !important; margin: 0 !important;
            letter-spacing: 1px; font-weight: 700; text-align: center !important; }
        .header-banner p { margin: 5px 0 0 0 !important; font-size: 14px; opacity: 0.9; text-align: center !important; }
    </style>
""", unsafe_allow_html=True)


# ======================= COLUNAS DA PLANILHA =======================
# chave: (termos que devem aparecer no cabeçalho, posição padrão caso o cabeçalho não seja encontrado)
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
COR_GRUPO = {"Aguardando recebimento": "#6aa84f", "Registro CRM": "#e0a800", "Verificação": "#4a7fcb",
             "Aprovação": "#4a7fcb", "Aguardando aceite": "#e69138", "Em reparo": "#e69138",
             "Reprovado": "#e53935", "Devolução": "#6b7280", "Concluído": "#1e8e3e", "Dados incompletos": "#9aa7ba"}


def _norm(t):
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def parse_data(s):
    """Lê datas como '8-jan.-26' ou '08/04/2026'. Anos antes de 2020 são tratados como erro de digitação."""
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
    """Fluxo sequencial em 8 fases. Cada fase só é 'ok' se as anteriores também estiverem."""
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
        est[4], est[5] = "bloqueado", "pulado"
    fora = (f("data_reparo") and not (p5 or bloq)) or (f("solic_devol") and not (p6 or bloq))
    aviso = " ⚠️ Há campos preenchidos fora da sequência do fluxo; confira a planilha." if fora else ""

    def res(status, grupo, detalhe, faltam=()):
        return {"estados": est, "status": status, "grupo": grupo, "detalhe": detalhe + aviso,
                "faltam": list(faltam), "concluido": bool(p8)}

    def devolucao():
        extra = f" Status da devolução: {r['status_devol']}." if r["status_devol"] else ""
        extra += f" Status de conclusão: {r['status_concl']}." if r["status_concl"] else ""
        return res("🚚 Devolução em andamento", "Devolução",
                   "Devolução solicitada." + extra, falta(["status_concl"]))

    if not p1:
        return res("⚪ Dados incompletos", "Dados incompletos", "O registro não tem o nome do cliente.", falta(["cliente"]))
    if not p2:
        return res("⚪ Aguardando recebimento (Logística)", "Aguardando recebimento",
                   "Cliente registrado. Falta confirmar o recebimento do material na Logística.", falta(req2))
    if not p3:
        return res("🟡 Aguardando registro no CRM (Serviços)", "Registro CRM",
                   "Material recebido pela Logística. Aguardando o registro no CRM e a Service Notification pelo setor de Serviços.",
                   falta(req3))
    if not p4:
        return res("🔵 Em verificação / diagnóstico", "Verificação",
                   "Registro concluído. O equipamento está em verificação.", falta(["data_verif"]))
    if not (p5 or bloq):
        atual = f" Valor atual na planilha: '{r['aprovacao']}'." if r["aprovacao"] else ""
        return res("🟠 Aguardando aprovação do relatório", "Aprovação",
                   f"Verificação concluída em {r['data_verif']}. Aguardando a decisão do relatório "
                   f"(Aprovado, Cortesia, Garantia ou Reprovado).{atual}", falta(["aprovacao"]))
    if bloq:
        if not p7:
            return res("🔴 Relatório reprovado — aguardando solicitação de devolução", "Reprovado",
                       "Relatório reprovado: não haverá reparo. Falta solicitar a devolução do equipamento.",
                       falta(["solic_devol"]))
        return devolucao() if not p8 else res("🟢 Concluído", "Concluído", "Equipamento devolvido sem reparo. Processo concluído.")
    if not p6:
        if not dispensa and not f("aceite"):
            return res("🟠 Aguardando aceite do pedido (Ordem SAP)", "Aguardando aceite",
                       f"Relatório {r['aprovacao']}. Falta o aceite do pedido (Ordem SAP) para iniciar o reparo.",
                       falta(["aceite"]))
        return res("🛠️ Em reparo", "Em reparo",
                   f"Relatório {r['aprovacao']}"
                   + (" (sem pedido)" if dispensa else f"; pedido {r['aceite']} aceito") + ". Equipamento em reparo.",
                   falta(["data_reparo"]))
    if not p7:
        return res("📦 Reparo finalizado — aguardando solicitação de devolução", "Devolução",
                   f"Reparo finalizado em {r['data_reparo']}. Falta solicitar a devolução ao cliente.",
                   falta(["solic_devol"]))
    if not p8:
        return devolucao()
    return res("🟢 Concluído", "Concluído", "Equipamento reparado, devolvido e processo concluído.")


# ======================= LEITURA DA PLANILHA =======================
@st.cache_resource
def conectar():
    info_json = st.secrets["gspread_credentials"]["json_string"]
    dic_chaves = json.loads(info_json, strict=False)
    if "private_key" in dic_chaves:
        dic_chaves["private_key"] = dic_chaves["private_key"].replace("\\\\n", "\n").replace("\\n", "\n")
    return gspread.service_account_from_dict(dic_chaves).open_by_url(LINK_PLANILHA)


@st.cache_data(ttl=300)
def ler_valores():
    return conectar().worksheet(NOME_DA_ABA).get_all_values()


def ler_casos(valores, hoje):
    ini, cab = 4, None  # padrão: cabeçalho na linha 4
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
    for l in valores[ini:]:
        reg = {k: (l[j].strip() if j is not None and j < len(l) else "") for k, j in idx.items()}
        reg = {k: ("" if v in ("-", "—") else v) for k, v in reg.items()}
        if reg["cliente"] or reg["nf"]:
            linhas.append(reg)
    df = pd.DataFrame(linhas, columns=list(CAMPOS))
    av = [avaliar(r) for r in df.to_dict("records")]
    df["status"] = [a["status"] for a in av]
    df["grupo"] = [a["grupo"] for a in av]
    df["concluido"] = [a["concluido"] for a in av]
    df["_aval"] = av
    dt = df["data_receb"].map(parse_data)
    df["dias"] = [(hoje - d).days if pd.notna(d) and not c else None for d, c in zip(dt, df["concluido"])]
    return df, ausentes


# ======================= VISUAL (HTML) =======================
def compacta(h):
    return "\n".join(l.strip() for l in h.splitlines() if l.strip())


CSS_TL = """<style>
.stepper-wrapper{position:relative;display:flex;justify-content:space-between;margin:28px 0 14px}
.stepper-wrapper::before{content:"";position:absolute;top:15px;left:0;width:100%;height:12px;background:#dbdbdb;border-radius:10px;z-index:1}
.stepper-line-active{position:absolute;top:15px;left:0;height:12px;background:linear-gradient(90deg,#00c732,#00e63d);border-radius:10px;z-index:2;transition:width .6s}
.stepper-item{position:relative;display:flex;flex-direction:column;align-items:center;flex:1;z-index:3}
.step-counter{width:42px;height:42px;display:flex;justify-content:center;align-items:center;background:#fff;border:4px solid #cbd6e2;border-radius:50%;font-weight:bold;color:#cbd6e2;font-size:17px;box-shadow:0 2px 5px rgba(0,0,0,.1);box-sizing:border-box}
.step-name{margin-top:8px;font-size:12px;font-weight:600;color:#5c6b73;text-align:center;padding:0 4px}
.stepper-item.completed .step-counter{background:#00e63d;border-color:#00e63d;color:#fff}
.stepper-item.completed .step-name{color:#1b4f93;font-weight:700}
.stepper-item.blocked .step-counter{background:#e53935;border-color:#e53935;color:#fff}
.stepper-item.blocked .step-name{color:#e53935;font-weight:700}
.stepper-item.skipped .step-counter{background:#e1e9f5;border-color:#c5d3e8;color:#7d8fa8}
</style>"""


def html_timeline(estados):
    n = len(estados)
    ult = 0
    for i, e in enumerate(estados):
        if e == "pendente":
            break
        if e in ("ok", "bloqueado"):
            ult = i + 1
    largura = 0 if ult == 0 else 100 if ult == n else (ult - 0.5) / n * 100
    fundo = ""
    if "bloqueado" in estados and largura:
        pct = min((estados.index("bloqueado") + 0.5) / n * 100 / largura * 100, 100)
        fundo = f"background:linear-gradient(90deg,#e53935 {pct:.1f}%,#00c732 {pct:.1f}%,#00e63d 100%);"
    itens = ""
    for nome, e in zip(NOMES_FASES, estados):
        cls = {"ok": "completed", "bloqueado": "blocked", "pulado": "skipped", "pendente": ""}[e]
        ic = {"ok": "✓", "bloqueado": "✕", "pulado": "–", "pendente": ""}[e]
        itens += f'<div class="stepper-item {cls}"><div class="step-counter">{ic}</div><div class="step-name">{nome}</div></div>'
    return compacta(CSS_TL + f'<div class="stepper-wrapper"><div class="stepper-line-active" style="width:{largura:.2f}%;{fundo}"></div>{itens}</div>')


CSS_CARDS = """<style>
.hb-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:8px}
@media(max-width:900px){.hb-grid{grid-template-columns:1fr}}
.hb-card{background:#fff;border-radius:12px;padding:14px 16px;box-shadow:0 3px 10px rgba(27,79,147,.10);border-top:5px solid #4f83cc;text-align:left}
.hb-h{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px;font-weight:800;letter-spacing:.5px;text-transform:uppercase;color:#2c3e57;margin-bottom:8px}
.hb-h em{font-style:normal;font-size:11px;font-weight:700;padding:3px 8px;border-radius:20px;white-space:nowrap;text-transform:none;letter-spacing:0}
.hb-h em.ok{background:#e3f6e8;color:#1e8e3e}.hb-h em.bad{background:#fde2e2;color:#c62828}
.hb-h em.skip{background:#e8edf5;color:#6b7a90}.hb-h em.part{background:#fff3d6;color:#b45309}.hb-h em.pend{background:#eef2f8;color:#6b7a90}
.hb-row{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px dashed #e1e9f5;font-size:13px}
.hb-row:last-child{border-bottom:none}
.hb-row span{color:#6b7a90}.hb-row b{color:#1b4f93;text-align:right;overflow-wrap:anywhere}.hb-row b.vz{color:#b3bdcc;font-weight:500}
.hb-chips{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin:6px 0 4px}
.hb-chip{background:#fff;border-radius:20px;padding:6px 14px;font-size:13px;color:#2c3e57;box-shadow:0 2px 6px rgba(27,79,147,.10)}
.hb-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:8px 0 14px}
.hb-kpi{background:#fff;border-radius:10px;padding:12px;text-align:center;box-shadow:0 3px 10px rgba(27,79,147,.10);border-top:5px solid #4f83cc}
.hb-kpi .n{font-size:30px;font-weight:700;color:#1b4f93;line-height:1.1}.hb-kpi .l{font-size:12px;color:#6b7a90;margin-top:2px}
.tb{max-height:620px;overflow:auto;border-radius:8px;background:#fff;box-shadow:0 2px 6px rgba(0,0,0,.08)}
.tb table{width:100%;border-collapse:collapse;font-size:14px}
.tb th{position:sticky;top:0;background:#1b4f93;color:#fff;padding:10px 8px;z-index:1;text-align:center}
.tb td{padding:8px;border-bottom:1px solid #e4e9f0;text-align:center}
</style>"""

FASES_CARDS = [
    ("Registro de Clientes", "#8b4513", [0], [("ordem_status",), ("cliente",)]),
    ("Recebimento | Logística", "#6aa84f", [1], [("nf",), ("itens",), ("data_nf",), ("data_receb",)]),
    ("Recebimento | Serviços", "#e0a800", [2], [("data_crm",), ("cas",), ("service",)]),
    ("Verificações & Diagnósticos", "#4a7fcb", [3, 4], [("data_verif",), ("status_verif",), ("aprovacao",)]),
    ("Reparos & Manutenções", "#e69138", [5], [("aceite",), ("data_ordem",), ("data_reparo",)]),
    ("Devolução | Logística", "#6b7280", [6, 7], [("solic_devol",), ("status_devol",), ("status_concl",)]),
]


def _badge(estados, idx):
    s = [estados[i] for i in idx]
    if "bloqueado" in s:
        return "✕ Bloqueado", "bad"
    if all(x == "ok" for x in s):
        return "✓ Concluída", "ok"
    if all(x == "pulado" for x in s):
        return "– Não se aplica", "skip"
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
    cards = f'<div class="hb-kpi" style="border-top-color:#1b4f93"><div class="n">{len(abertos)}</div><div class="l">Em andamento</div></div>'
    for g in COR_GRUPO:
        if cont.get(g, 0):
            cards += f'<div class="hb-kpi" style="border-top-color:{COR_GRUPO[g]}"><div class="n">{int(cont[g])}</div><div class="l">{g}</div></div>'
    return compacta(CSS_CARDS + f'<div class="hb-kpis">{cards}</div>')


def html_tabela(df):
    cols = [("Cliente", "cliente"), ("NF", "nf"), ("CAS-SO-BR", "cas"), ("Recebimento", "data_receb"),
            ("Dias", "dias"), ("Status", "status"), ("Ordem Status", "ordem_status")]
    th = "".join(f"<th>{n}</th>" for n, _ in cols)
    linhas = ""
    for r in df.to_dict("records"):
        d = r["dias"]
        cor = ("#fde2e2" if d >= ALERTA_VERMELHO_DIAS else "#fff3d6" if d >= ALERTA_AMARELO_DIAS else "") if pd.notna(d) else ""
        tds = ""
        for _, k in cols:
            v = r[k]
            v = "-" if (v is None or (isinstance(v, float) and pd.isna(v)) or v == "") else (f"{int(v)}" if k == "dias" else v)
            tds += f"<td>{esc(str(v))}</td>"
        linhas += f'<tr style="background:{cor}">{tds}</tr>'
    return compacta(CSS_CARDS + f'<div class="tb"><table><thead><tr>{th}</tr></thead><tbody>{linhas}</tbody></table></div>')


def card_aviso(titulo, sub):
    return compacta(f"""<div style="background:#fff4e5;border:2px solid #f5a623;border-radius:12px;padding:24px 20px;text-align:center;
        box-shadow:0 4px 12px rgba(245,166,35,.18);margin-top:10px"><div style="font-size:34px;line-height:1">⚠️</div>
        <div style="font-size:22px;font-weight:800;color:#b45309;margin-top:8px">{titulo}</div>
        <div style="font-size:15px;color:#5c6b73;margin-top:8px">{sub}</div></div>""")


# ======================= TELA =======================
st.markdown("""
    <div class="header-banner">
        <h1>HUB DE CONSERTO</h1>
        <p>Acompanhamento de equipamentos em reparo — do registro à devolução</p>
    </div>
""", unsafe_allow_html=True)

hoje = pd.Timestamp.today().normalize()
try:
    casos, ausentes = ler_casos(ler_valores(), hoje)
except Exception as e:
    st.error(f"Erro ao conectar com a planilha: {e}")
    st.stop()

c1, c2 = st.columns([6, 1])
c1.success("Conexão estabelecida com a planilha de Conserto!")
if c2.button("🔄 Atualizar"):
    st.cache_data.clear()
    st.rerun()
if ausentes:
    st.warning("Não encontrei no cabeçalho da planilha as colunas: " + ", ".join(ausentes)
               + ". Confira se os nomes não foram alterados.")

aba1, aba2 = st.tabs(["🔎 Consulta de Equipamento", "📋 Casos em andamento"])

# -------- 1. CONSULTA --------
with aba1:
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
                st.markdown(compacta(CSS_CARDS + f'<div class="hb-chips"><span class="hb-chip">📅 Recebido em <b>{esc(r["data_receb"] or "—")}</b></span>'
                                     f'<span class="hb-chip">⏱️ Em atendimento há <b>{dias}</b></span></div>'), unsafe_allow_html=True)
                st.markdown("### ⏳ Fluxo do Conserto:")
                st.markdown(html_timeline(av["estados"]), unsafe_allow_html=True)
                st.markdown("---")
                st.markdown("### 🔍 Detalhes por etapa:")
                st.markdown(html_cards(r, av["estados"]), unsafe_allow_html=True)

# -------- 2. EM ANDAMENTO --------
with aba2:
    st.markdown(html_kpis(casos), unsafe_allow_html=True)
    f1, f2, f3, f4 = st.columns(4)
    sel_g = f1.multiselect("Situação", [g for g in COR_GRUPO if g in set(casos["grupo"])])
    sel_c = f2.multiselect("Cliente", sorted(casos["cliente"].unique()))
    sel_o = f3.multiselect("Ordem Status", sorted(x for x in casos["ordem_status"].unique() if x))
    inc = f4.toggle("Incluir concluídos")
    v = casos if inc else casos[~casos["concluido"]]
    if sel_g: v = v[v["grupo"].isin(sel_g)]
    if sel_c: v = v[v["cliente"].isin(sel_c)]
    if sel_o: v = v[v["ordem_status"].isin(sel_o)]
    st.caption(f"{len(v)} caso(s) · amarelo ≥ {ALERTA_AMARELO_DIAS} dias · vermelho ≥ {ALERTA_VERMELHO_DIAS} dias desde o recebimento")
    v = v.sort_values("dias", ascending=False, na_position="last")
    st.markdown(html_tabela(v), unsafe_allow_html=True)

st.markdown("Hub de Conserto · Desenvolvido por Diego Elvis", unsafe_allow_html=True)
