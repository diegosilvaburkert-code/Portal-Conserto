import json
import re
import unicodedata
from html import escape as esc

import gspread
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ======================= CONFIGURAÇÃO =======================
LINK_PLANILHA = "COLE_AQUI_O_LINK_DA_PLANILHA_DE_CONSERTO"
NOME_DA_ABA = "Lead Time"

# Regras do fluxo (ajuste aqui se a regra do seu processo for diferente)
APROVACOES_OK = {"aprovado", "cortesia", "garantia"}      # relatório aprovado -> segue para reparo
APROVACOES_REPROVADAS = {"reprovado"}                      # relatório reprovado -> sem reparo, vai para devolução
APROVACOES_SEM_PEDIDO = {"cortesia", "garantia"}           # não exigem Aceite de Pedido (Ordem SAP)
CONCLUIDOS = {"concluido", "finalizado"}                   # "Status de conclusão" que ENCERRA o caso, mesmo com etapas não realizadas
ALERTA_AMARELO_DIAS = 30                                   # dias desde o recebimento
ALERTA_VERMELHO_DIAS = 60

st.set_page_config(page_title="Hub de Conserto", page_icon="🛠️", layout="wide")

st.markdown("""
    <style>
        :root{--azul:#7c93fb;--azul-esc:#4f67e8;--rosa:#f06aa8}
        .stApp{background:linear-gradient(135deg,#f0f3ff 0%,#e3e9fd 100%)}
        header[data-testid="stHeader"]{background:transparent}
        .block-container{padding-top:1.6rem;max-width:1500px}
        .header-banner{background:linear-gradient(110deg,#8aa0ff,#4f67e8);border-radius:22px;padding:28px;
            box-shadow:0 14px 32px rgba(79,103,232,.30)}
        .stAlert{border-radius:16px}

        /* ===== MENU LATERAL FIXO (sempre aberto) ===== */
        section[data-testid="stSidebar"]{
            width:270px!important;min-width:270px!important;max-width:270px!important;
            transform:none!important;visibility:visible!important;
            background:#ffffff;border-radius:0 26px 26px 0;box-shadow:8px 0 30px rgba(79,103,232,.14);
            position: relative !important;
        }
        [data-testid="stSidebarCollapseButton"],[data-testid="stSidebarCollapsedControl"],
        [data-testid="collapsedControl"],[data-testid="stSidebarResizeHandle"]{display:none!important}
        [data-testid="stSidebarUserContent"]{padding:1.2rem 1rem 3.5rem 1rem!important}

        .sb-logo{display:flex;align-items:center;gap:12px;padding:4px 10px 20px;white-space:nowrap;
            font-size:28px;border-bottom:1px solid #e8edfb;margin-bottom:16px}
        .sb-logo span{font-size:12px;font-weight:800;letter-spacing:1.6px;line-height:1.25;color:#4f67e8}

        section[data-testid="stSidebar"] .stButton{margin-bottom:6px}
        section[data-testid="stSidebar"] .stButton button{
            width:100%;justify-content:flex-start;border:none;border-radius:14px;
            padding:.75rem 1rem;background:transparent;box-shadow:none;transition:background .2s}
        .stApp section[data-testid="stSidebar"] .stButton button p{
            text-align:left!important;font-size:15px;font-weight:600;color:#5b6b8c;margin:0}
        section[data-testid="stSidebar"] .stButton button:hover{background:#eef1ff}
        section[data-testid="stSidebar"] .stButton button[kind="primary"],
        section[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"]{
            background:linear-gradient(90deg,#8aa0ff,#4f67e8);box-shadow:0 8px 18px rgba(79,103,232,.32)}
        .stApp section[data-testid="stSidebar"] .stButton button[kind="primary"] p,
        .stApp section[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"] p{color:#fff}
        
        /* Container Customizado do Status de Conexão na Sidebar */
        .sidebar-status-container {
            position: absolute;
            bottom: 15px;
            left: 15px;
            background-color: #d1e7dd;
            color: #0f5132;
            padding: 4px 10px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 700;
            border: 1px solid #badbcc;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
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
        est[4], est[5] = "bloqueado", "nao_realizado"
    fora = (f("data_reparo") and not (p5 or bloq)) or (f("solic_devol") and not (p6 or bloq))
    aviso = " ⚠️ Há campos preenchidos fora da sequência do fluxo; confira a planilha." if fora else ""

    # Status de conclusão = Finalizado/Concluído encerra o caso: as etapas não realizadas são desconsideradas
    final = p1 and concl
    if final:
        # cada etapa vale pelo que está preenchida nela; o que não foi feito vira "–" (desconsiderado)
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
            d += (" Etapas não realizadas (desconsideradas): " + ", ".join(nao_feitas) + ".") if nao_feitas \
                else " Todas as etapas foram concluídas."
        if r["status_devol"]:
            d += f" Status da devolução: {r['status_devol']}."
        return res("🟢 Finalizado", "Concluído", d)

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
.seg{position:absolute;top:15px;height:12px;z-index:2}
.stepper-item{position:relative;display:flex;flex-direction:column;align-items:center;flex:1;z-index:3}
.step-counter{width:42px;height:42px;display:flex;justify-content:center;align-items:center;background:#fff;border:4px solid #cbd6e2;border-radius:50%;font-weight:bold;color:#cbd6e2;font-size:17px;box-shadow:0 2px 5px rgba(0,0,0,.1);box-sizing:border-box}
.step-name{margin-top:8px;font-size:12px;font-weight:600;color:#5c6b73;text-align:center;padding:0 4px}
.stepper-item.completed .step-counter{background:#00d83a;border-color:#00d83a;color:#fff}
.stepper-item.completed .step-name{color:#1b4f93;font-weight:700}
.stepper-item.blocked .step-counter{background:#e53935;border-color:#e53935;color:#fff}
.stepper-item.blocked .step-name{color:#e53935;font-weight:700}
.stepper-item.warn .step-counter{background:#f5b301;border-color:#f5b301;color:#fff}
.stepper-item.warn .step-name{color:#b7791f;font-weight:700}
.stepper-item.skipped .step-counter{background:#e1e9f5;border-color:#c5d3e8;color:#7d8fa8}
</style>"""

# cor do trecho da barra que CHEGA em cada bolinha (parte do centro da bolinha anterior até o centro desta)
COR_TRECHO = {"ok": "#00d83a", "bloqueado": "#e53935", "nao_realizado": "#f5b301", "pulado": "#c5d3e8"}


def html_timeline(estados):
    n = len(estados)
    segs = ""
    for k, e in enumerate(estados, start=1):
        cor = COR_TRECHO.get(e)
        if not cor:
            continue
        ini = 0 if k == 1 else (k - 1.5) / n * 100
        larg = (0.5 if k == 1 else 1) / n * 100 + 0.15
        raio = "10px 0 0 10px" if k == 1 else "0"
        segs += f'<div class="seg" style="left:{ini:.3f}%;width:{larg:.3f}%;background:{cor};border-radius:{raio}"></div>'
    if estados[-1] == "ok":
        segs += f'<div class="seg" style="left:{(n - 0.5) / n * 100:.3f}%;width:{0.5 / n * 100:.3f}%;background:{COR_TRECHO["ok"]};border-radius:0 10px 10px 0"></div>'
    itens = ""
    for nome, e in zip(NOMES_FASES, estados):
        cls = {"ok": "completed", "bloqueado": "blocked", "nao_realizado": "warn", "pulado": "skipped", "pendente": ""}[e]
        ic = {"ok": "✓", "bloqueado": "✕", "nao_realizado": "!", "pulado": "–", "pendente": ""}[e]
        if e == "nao_realizado":
            nome = f"{nome} não realizado"
        itens += f'<div class="stepper-item {cls}"><div class="step-counter">{ic}</div><div class="step-name">{nome}</div></div>'
    return compacta(CSS_TL + f'<div class="stepper-wrapper">{segs}{itens}</div>')


CSS_CARDS = """<style>
.hb-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:8px}
@media(max-width:900px){.hb-grid{grid-template-columns:1fr}}
.hb-card{background:#fff;border-radius:12px;padding:14px 16px;box-shadow:0 3px 10px rgba(27,79,147,.10);border-top:5px solid #4f83cc;text-align:left}
.hb-h{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px;font-weight:800;letter-spacing:.5px;text-transform:uppercase;color:#2c3e57;margin-bottom:8px}
.hb-h em{font-style:normal;font-size:11px;font-weight:700;padding:3px 8px;border-radius:20px;white-space:nowrap;text-transform:none;letter-spacing:0}
.hb-h em.ok{background:#e3f6e8;color:#1e8e3e}.hb-h em.bad{background:#fde2e2;color:#c62828}
.hb-h em.skip{background:#e8edf5;color:#6b7a90}.hb-h em.warn{background:#fff3d6;color:#b45309}.hb-h em.part{background:#fff3d6;color:#b45309}.hb-h em.pend{background:#eef2f8;color:#6b7a90}
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
    cards = f'<div class="hb-kpi" style="border-top-color:#1b4f93"><div class="n">{len(abertos)}</div><div class="l">Em andamento</div></div>'
    for g in COR_GRUPO:
        if cont.get(g, 0):
            cards += f'<div class="hb-kpi" style="border-top-color:{COR_GRUPO[g]}"><div class="n">{int(cont[g])}</div><div class="l">{g}</div></div>'
    return compacta(CSS_CARDS + f'<div class="hb-kpis">{cards}</div>')


_CSS_TAB = """
body{margin:0;background:transparent;font-family:"Source Sans Pro",Arial,sans-serif;color:#2c3e57}
.top{display:flex;justify-content:space-between;align-items:center;padding:2px 4px 8px;font-size:13px;color:#6b7a90}
.top button{border:1px solid #1b4f93;background:#fff;color:#1b4f93;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px}
.top button:hover{background:#1b4f93;color:#fff}
.tb{max-height:600px;overflow:auto;border-radius:8px;background:#fff;box-shadow:0 2px 6px rgba(0,0,0,.08)}
table{width:100%;border-collapse:collapse;font-size:14px}
.hd th{position:sticky;top:0;height:41px;box-sizing:border-box;background:#1b4f93;color:#fff;padding:0 8px;z-index:2;cursor:pointer;user-select:none;white-space:nowrap}
.hd th:hover{background:#2a62ad}
.ft td{position:sticky;top:41px;background:#eaf1fb;padding:5px 6px;z-index:2;border-bottom:1px solid #d5e1f2}
.ft input,.ft select{width:100%;box-sizing:border-box;padding:4px 6px;border:1px solid #c5d3e8;border-radius:5px;font-size:12px;background:#fff;color:#2c3e57}
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
    """Tabela com ordenação por clique no título e filtros por coluna (roda dentro de um iframe)."""
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

# ======================= MENU LATERAL =======================
with st.sidebar:
    st.markdown("""
        <div style="text-align:center; padding:20px 0;">
            <h3 style="color:#315ce8; margin-bottom:5px;">
                🛠️ HUB DE CONSERTO
            </h3>
            <p style="font-size:12px; color:#64748b;">
                Acompanhamento logístico
            </p>
        </div>
        <hr style="border-color:#e2e8f0;">
    """, unsafe_allow_html=True)

    pagina = st.radio(
        "Navegação",
        [
            "🔎 Consulta de Equipamento",
            "📋 Casos em andamento"
        ],
        label_visibility="collapsed",
        key="menu_hub_conserto"
    )

    st.markdown("""
        <div style="
            position:fixed;
            bottom:20px;
            left:20px;
            width:200px;
            color:#16a34a;
            font-size:12px;
            padding:10px;
            background:#f0fdf4;
            border-radius:8px;
            text-align:center;
        ">
            🟢 Conexão Estabelecida
        </div>
    """, unsafe_allow_html=True)
    
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

if ausentes:
    st.warning("Não encontrei no cabeçalho da planilha as colunas: " + ", ".join(ausentes)
               + ". Confira se os nomes não foram alterados.")

# -------- 1. CONSULTA --------
if pagina == "🔎 Consulta de Equipamento":
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
elif pagina == "📋 Casos em andamento":
    st.markdown(html_kpis(casos), unsafe_allow_html=True)
    inc = st.toggle("Incluir casos finalizados")
    v = casos if inc else casos[~casos["concluido"]]
    st.caption(f"Clique no título de uma coluna para ordenar · use os campos abaixo dos títulos para filtrar · "
               f"amarelo ≥ {ALERTA_AMARELO_DIAS} dias · vermelho ≥ {ALERTA_VERMELHO_DIAS} dias desde o recebimento")
    v = v.sort_values("dias", ascending=False, na_position="last")
    components.html(html_tabela_interativa(v), height=690, scrolling=False)

st.markdown("Hub de Conserto · Desenvolvido por Diego Elvis", unsafe_allow_html=True)
