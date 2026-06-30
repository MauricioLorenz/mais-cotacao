"""
Sistema de Cotação Automática de Shows — Mais Show / Grupo Be
Arquitetura: 5 agentes (Coordenador, Disponibilidade, Logística, Finanças, Formatação)
Execução: uvicorn main:app --reload  |  python main.py
"""

from __future__ import annotations

import os
import unicodedata
from datetime import date, datetime, timedelta
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

# ---------------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Cotação Shows — Mais Show",
    description="API de cotação automática de shows artísticos baseada em regras de negócio.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO GLOBAL — editável via POST /config em tempo de execução
# ---------------------------------------------------------------------------

CONFIG: dict = {
    "nome_artista": "GICA",
    "cache_base": {
        "ticket": 25000.0,
        "privado": 40000.0,
        "prefeitura": 80000.0,
    },
    "custo_km": 9.0,
    "limite_terrestre_km": 800,
    "despesas_locais": {
        "qtd_pessoas": 8,
        "qtd_diarias": 1,
        "valor_diaria_alimentacao_por_pessoa": 120,
        "total_alimentacao": 960,
        "hospedagem": {
            "qtd_suites": 1,
            "qtd_singles": 2,
            "qtd_duplos": 2,
            "qtd_triplos": 1,
        },
        "traslado_local": {
            "qtd_vans": 1,
            "qtd_carros_apoio": 1,
        },
        "qtd_camarins_exigidos": 2,
        "qtd_carregadores": 4,
        "total_despesas_locais_estimado": 960,
    },
    # Cidade base de saída do artista quando não há roteiro adjacente (Cenário C)
    "cidade_base": "São Paulo",
    "uf_base": "SP",
    # Configuração do modelo de negócio para shows tipo "ticket"
    "modelo_ticket": {
        "tipo": "garantia_porta",  # "garantia_porta" | "fixo"
        "garantia_minima": 18000.0,
        "percentual_porta": 50,
        "prazo_prestacao_contas_horas": 48,
    },
}

# ---------------------------------------------------------------------------
# CONSTANTES IMUTÁVEIS
# ---------------------------------------------------------------------------

AGENDA_OCUPADA: dict[str, str] = {
    "10/07/2026": "Curitiba/PR",
    "15/07/2026": "Rio de Janeiro/RJ",
    "25/07/2026": "Belo Horizonte/MG",
}

DISTANCIAS_DE_SP: dict[str, int] = {
    "campinas": 100,
    "ribeirao preto": 310,
    "curitiba": 410,
    "florianopolis": 700,
    "porto alegre": 1100,
    "belo horizonte": 590,
    "rio de janeiro": 430,
    "salvador": 1950,
    "recife": 2700,
    "manaus": 3900,
}

FALLBACK_DISTANCIA_POR_UF: dict[str, int] = {
    "SP": 500, "MG": 500, "RJ": 500, "PR": 500, "SC": 500, "RS": 500,
    "BA": 2000, "PE": 2000, "AM": 2000,
}
FALLBACK_DISTANCIA_PADRAO = 800

HOJE = date.today()

# ---------------------------------------------------------------------------
# MODELOS PYDANTIC — ENTRADA
# ---------------------------------------------------------------------------


class CotacaoInput(BaseModel):
    """Payload de entrada para solicitação de cotação de show.
    Inclui campos opcionais de configuração — se informados, sobrescrevem
    o CONFIG global antes do cálculo (equivale a POST /config + POST /cotar em uma chamada).
    """

    # --- Dados do show (obrigatórios) ---
    tipo_show: str
    data_evento: str
    cidade: str
    estado: str
    local: Optional[str] = None
    capacidade_publico: Optional[int] = None
    hora_inicio: Optional[str] = None
    duracao_set: Optional[int] = None

    # --- Dados do contratante ---
    nome_contratante: str
    tipo_contratante: str
    cnpj_cpf: Optional[str] = None

    # --- Roteiro adjacente ---
    show_dia_anterior: bool
    cidade_dia_anterior: Optional[str] = None
    data_anterior: Optional[str] = None

    show_dia_seguinte: bool
    cidade_dia_seguinte: Optional[str] = None
    data_seguinte: Optional[str] = None

    # --- Configurações opcionais (sobrescrevem CONFIG se informadas) ---
    nome_artista: Optional[str] = None
    cache_ticket: Optional[float] = None
    cache_privado: Optional[float] = None
    cache_prefeitura: Optional[float] = None
    custo_km: Optional[float] = None
    limite_terrestre_km: Optional[int] = None
    cidade_base: Optional[str] = None
    uf_base: Optional[str] = None
    qtd_pessoas: Optional[int] = None
    valor_diaria_por_pessoa: Optional[float] = None
    qtd_camarins: Optional[int] = None
    qtd_carregadores: Optional[int] = None
    ticket_tipo: Optional[str] = None
    ticket_garantia_minima: Optional[float] = None
    ticket_percentual_porta: Optional[int] = None
    ticket_prazo_contas: Optional[int] = None

    @model_validator(mode="after")
    def validar_adjacentes(self) -> "CotacaoInput":
        if self.show_dia_anterior:
            if not self.cidade_dia_anterior:
                raise ValueError("cidade_dia_anterior é obrigatório quando show_dia_anterior=true")
            if not self.data_anterior:
                raise ValueError("data_anterior é obrigatório quando show_dia_anterior=true")
        if self.show_dia_seguinte:
            if not self.cidade_dia_seguinte:
                raise ValueError("cidade_dia_seguinte é obrigatório quando show_dia_seguinte=true")
            if not self.data_seguinte:
                raise ValueError("data_seguinte é obrigatório quando show_dia_seguinte=true")
        tipos_validos = {"ticket", "privado", "prefeitura"}
        if self.tipo_show not in tipos_validos:
            raise ValueError(f"tipo_show deve ser um de: {tipos_validos}")
        return self


class ConfigInput(BaseModel):
    """Campos editáveis da configuração global. Apenas campos informados são atualizados."""
    nome_artista: Optional[str] = None
    cache_ticket: Optional[float] = None
    cache_privado: Optional[float] = None
    cache_prefeitura: Optional[float] = None
    custo_km: Optional[float] = None
    limite_terrestre_km: Optional[int] = None
    qtd_pessoas: Optional[int] = None
    valor_diaria_por_pessoa: Optional[float] = None
    qtd_camarins: Optional[int] = None
    qtd_carregadores: Optional[int] = None
    # Modelo de ticket
    ticket_tipo: Optional[str] = None  # "garantia_porta" | "fixo"
    ticket_garantia_minima: Optional[float] = None
    ticket_percentual_porta: Optional[int] = None
    ticket_prazo_contas: Optional[int] = None
    # Cidade de origem do artista (Cenário C — show sem roteiro adjacente)
    cidade_base: Optional[str] = None
    uf_base: Optional[str] = None


# ---------------------------------------------------------------------------
# MODELOS PYDANTIC — SAÍDA
# ---------------------------------------------------------------------------


class HospedagemOutput(BaseModel):
    qtd_suites: int
    qtd_singles: int
    qtd_duplos: int
    qtd_triplos: int


class TrasladadoLocalOutput(BaseModel):
    qtd_vans: int
    qtd_carros_apoio: int


class DespesasLocaisOutput(BaseModel):
    qtd_pessoas: int
    qtd_diarias: int
    valor_diaria_alimentacao_por_pessoa: float
    total_alimentacao: float
    hospedagem: HospedagemOutput
    traslado_local: TrasladadoLocalOutput
    qtd_camarins_exigidos: int
    qtd_carregadores: int
    total_despesas_locais_estimado: float


class LogisticaOutput(BaseModel):
    modal: str
    cenario: str
    origem: str
    destino: str
    distancia_km: int
    custo_ida_volta: Optional[float]
    custo_aereo: Optional[str]
    logistica_inclusa_cache: bool
    observacao: str


class ModeloTicketOutput(BaseModel):
    tipo: str
    garantia_minima: float
    percentual_porta: int
    base_calculo: str
    logica: str
    auditoria_bilheteria: bool
    prazo_prestacao_contas_horas: int


class PropostaOutput(BaseModel):
    tipo_show: str
    cache_base_com_nf: float
    ajuste_urgencia_percentual: float
    cache_ajustado: float
    logistica: LogisticaOutput
    despesas_locais: DespesasLocaisOutput
    total_proposta: float
    modelo_ticket: Optional[ModeloTicketOutput]
    validade_dias_uteis: int
    prazo_minimo_contrato_dias: int
    condicoes_pagamento: str
    nota_fiscal: bool


class CotacaoResponse(BaseModel):
    status: str
    motivo_negacao: Optional[str] = None
    sugestoes: Optional[list[str]] = None
    proposta: Optional[PropostaOutput] = None
    resumo_natural: Optional[str] = None


# ---------------------------------------------------------------------------
# FUNÇÕES AUXILIARES
# ---------------------------------------------------------------------------


def normalizar(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def parse_date(s: str) -> date:
    s = s.strip()
    # Aceita ano com 2 dígitos: "DD/MM/AA" → "DD/MM/20AA"
    parts = s.split("/")
    if len(parts) == 3 and len(parts[2]) == 2:
        parts[2] = "20" + parts[2]
        s = "/".join(parts)
    return datetime.strptime(s, "%d/%m/%Y").date()


def formatar_data(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def sugerir_datas_alternativas(data_base: date) -> list[str]:
    candidatas = [
        data_base - timedelta(days=1),
        data_base + timedelta(days=1),
        data_base + timedelta(days=7),
    ]
    sugestoes = []
    for d in candidatas:
        chave = formatar_data(d)
        if chave not in AGENDA_OCUPADA:
            sugestoes.append(chave)
    return sugestoes[:3]


def calcular_distancia(
    cidade_origem: str, uf_origem: str,
    cidade_destino: str, uf_destino: str,
) -> tuple[int, bool]:
    orig_norm = normalizar(cidade_origem)
    dest_norm = normalizar(cidade_destino)

    if orig_norm in ("sao paulo", "são paulo", "sp"):
        if dest_norm in DISTANCIAS_DE_SP:
            return DISTANCIAS_DE_SP[dest_norm], False
        uf = uf_destino.upper()
        return FALLBACK_DISTANCIA_POR_UF.get(uf, FALLBACK_DISTANCIA_PADRAO), True

    if dest_norm in ("sao paulo", "são paulo", "sp"):
        if orig_norm in DISTANCIAS_DE_SP:
            return DISTANCIAS_DE_SP[orig_norm], False
        uf = uf_origem.upper()
        return FALLBACK_DISTANCIA_POR_UF.get(uf, FALLBACK_DISTANCIA_PADRAO), True

    dist_orig = DISTANCIAS_DE_SP.get(orig_norm)
    dist_dest = DISTANCIAS_DE_SP.get(dest_norm)

    if dist_orig and dist_dest:
        estimativa = int((dist_orig + dist_dest) / 1.3)
        return estimativa, False

    uf = uf_destino.upper()
    return FALLBACK_DISTANCIA_POR_UF.get(uf, FALLBACK_DISTANCIA_PADRAO), True


# ---------------------------------------------------------------------------
# AGENTE 1 — DISPONIBILIDADE
# ---------------------------------------------------------------------------


def agente_disponibilidade(data_evento: str) -> dict:
    print(f"\n[AGENTE DISPONIBILIDADE] Verificando data: {data_evento}")

    if data_evento in AGENDA_OCUPADA:
        cidade_ocupada = AGENDA_OCUPADA[data_evento]
        data_obj = parse_date(data_evento)
        sugestoes = sugerir_datas_alternativas(data_obj)
        print(f"  ✗ Data OCUPADA — compromisso em {cidade_ocupada}")
        return {"disponivel": False, "cidade_ocupada": cidade_ocupada, "sugestoes": sugestoes}

    print("  ✓ Data DISPONÍVEL")
    return {"disponivel": True, "cidade_ocupada": None, "sugestoes": []}


# ---------------------------------------------------------------------------
# AGENTE 2 — LOGÍSTICA
# ---------------------------------------------------------------------------


def agente_logistica(
    cidade: str,
    estado: str,
    show_dia_anterior: bool,
    cidade_dia_anterior: Optional[str],
    show_dia_seguinte: bool,
    cidade_dia_seguinte: Optional[str],
    tipo_show: str,
) -> dict:
    print(f"\n[AGENTE LOGÍSTICA] Cidade: {cidade}/{estado} | Tipo: {tipo_show}")

    logistica_inclusa = tipo_show == "prefeitura"

    if show_dia_anterior and cidade_dia_anterior:
        cenario = "A"
        origem = cidade_dia_anterior
        destino = f"{cidade}/{estado}"
        distancia_km, fallback = calcular_distancia(cidade_dia_anterior, estado, cidade, estado)
        ida_e_volta = False
        obs = f"Roteiro: saindo de {cidade_dia_anterior} para {cidade}. Apenas trecho de ida."

    elif show_dia_seguinte and cidade_dia_seguinte:
        cenario = "B"
        origem = f"{cidade}/{estado}"
        destino = cidade_dia_seguinte
        distancia_km, fallback = calcular_distancia(cidade, estado, cidade_dia_seguinte, estado)
        ida_e_volta = False
        obs = f"Roteiro: saindo de {cidade} para {cidade_dia_seguinte} após o show. Apenas trecho."

    else:
        cenario = "C"
        cb = CONFIG["cidade_base"]
        ub = CONFIG["uf_base"]
        origem = f"{cb}/{ub}"
        destino = f"{cidade}/{estado}"
        distancia_km, fallback = calcular_distancia(cb, ub, cidade, estado)
        ida_e_volta = True
        obs = f"Show isolado. Ida e volta de {cb}/{ub} incluídas."

    if fallback:
        obs += f" (ATENÇÃO: distância estimada por fallback — cidade '{cidade}' não mapeada)"

    print(f"  Distância: {distancia_km} km | Fallback: {fallback}")

    limite_km = CONFIG["limite_terrestre_km"]
    custo_por_km = CONFIG["custo_km"]

    if distancia_km <= limite_km:
        modal = "terrestre"
        custo_base = distancia_km * custo_por_km
        custo_total = custo_base * 2 if ida_e_volta else custo_base
        custo_aereo = None
        print(f"  Modal: TERRESTRE | Custo: R$ {custo_total:.2f}")
    else:
        modal = "aereo"
        custo_total = None
        custo_aereo = "A cotar"
        print("  Modal: AÉREO | Custo: A cotar")

    return {
        "modal": modal,
        "cenario": cenario,
        "origem": origem,
        "destino": destino,
        "distancia_km": distancia_km,
        "custo_ida_volta": custo_total,
        "custo_aereo": custo_aereo,
        "logistica_inclusa_cache": logistica_inclusa,
        "observacao": obs,
    }


# ---------------------------------------------------------------------------
# AGENTE 3 — FINANÇAS
# ---------------------------------------------------------------------------


def agente_financas(
    tipo_show: str,
    data_evento: str,
    custo_logistico: Optional[float],
    logistica_inclusa: bool,
) -> dict:
    print(f"\n[AGENTE FINANÇAS] Tipo: {tipo_show}")

    cache = CONFIG["cache_base"][tipo_show]
    print(f"  Cache base: R$ {cache:.2f}")

    data_ev = parse_date(data_evento)
    dias_antecedencia = (data_ev - HOJE).days

    if dias_antecedencia > 60:
        ajuste_urg = 0.0
    elif dias_antecedencia >= 30:
        ajuste_urg = 0.05
    elif dias_antecedencia >= 15:
        ajuste_urg = 0.15
    else:
        ajuste_urg = 0.25

    cache_ajustado = cache * (1 + ajuste_urg)
    print(f"  Antecedência: {dias_antecedencia}d | Urgência: {ajuste_urg*100:.0f}%")
    print(f"  Cache ajustado: R$ {cache_ajustado:.2f}")

    custo_log_aplicado = 0.0
    if not logistica_inclusa and custo_logistico is not None:
        custo_log_aplicado = custo_logistico

    dl = CONFIG["despesas_locais"]
    total = cache_ajustado + custo_log_aplicado + dl["total_despesas_locais_estimado"]
    print(f"  Total proposta: R$ {total:.2f}")

    modelo_ticket = None
    mt_cfg = CONFIG["modelo_ticket"]
    if tipo_show == "ticket" and mt_cfg["tipo"] == "garantia_porta":
        modelo_ticket = {
            "tipo": "garantia_porta",
            "garantia_minima": mt_cfg["garantia_minima"],
            "percentual_porta": mt_cfg["percentual_porta"],
            "base_calculo": "bruto",
            "logica": (
                f"maior entre garantia_mínima (R$ {mt_cfg['garantia_minima']:,.2f}) "
                f"e ({mt_cfg['percentual_porta']}% × faturamento bruto)"
            ),
            "auditoria_bilheteria": True,
            "prazo_prestacao_contas_horas": mt_cfg["prazo_prestacao_contas_horas"],
        }
    # tipo "fixo": modelo_ticket fica None (sem bloco de garantia na proposta)

    return {
        "ajuste_urgencia_percentual": ajuste_urg * 100,
        "cache_ajustado": round(cache_ajustado, 2),
        "total_proposta": round(total, 2),
        "modelo_ticket": modelo_ticket,
        "validade_dias_uteis": 5,
        "prazo_minimo_contrato_dias": 30 if tipo_show == "prefeitura" else 21,
        "condicoes_pagamento": "50% entrada + 50% na data do show",
        "nota_fiscal": True,
    }


# ---------------------------------------------------------------------------
# AGENTE 4 — FORMATAÇÃO
# ---------------------------------------------------------------------------


def agente_formatacao(
    payload: CotacaoInput,
    logistica: dict,
    financas: dict,
) -> CotacaoResponse:
    print("\n[AGENTE FORMATAÇÃO] Montando proposta final...")

    dl = CONFIG["despesas_locais"]
    hosp = dl["hospedagem"]
    trasl = dl["traslado_local"]
    nome_artista = CONFIG["nome_artista"]

    despesas_out = DespesasLocaisOutput(
        qtd_pessoas=dl["qtd_pessoas"],
        qtd_diarias=dl["qtd_diarias"],
        valor_diaria_alimentacao_por_pessoa=dl["valor_diaria_alimentacao_por_pessoa"],
        total_alimentacao=dl["total_alimentacao"],
        hospedagem=HospedagemOutput(
            qtd_suites=hosp["qtd_suites"],
            qtd_singles=hosp["qtd_singles"],
            qtd_duplos=hosp["qtd_duplos"],
            qtd_triplos=hosp["qtd_triplos"],
        ),
        traslado_local=TrasladadoLocalOutput(
            qtd_vans=trasl["qtd_vans"],
            qtd_carros_apoio=trasl["qtd_carros_apoio"],
        ),
        qtd_camarins_exigidos=dl["qtd_camarins_exigidos"],
        qtd_carregadores=dl["qtd_carregadores"],
        total_despesas_locais_estimado=dl["total_despesas_locais_estimado"],
    )

    logistica_out = LogisticaOutput(**logistica)

    modelo_ticket_out = None
    if financas["modelo_ticket"]:
        modelo_ticket_out = ModeloTicketOutput(**financas["modelo_ticket"])

    proposta = PropostaOutput(
        tipo_show=payload.tipo_show,
        cache_base_com_nf=CONFIG["cache_base"][payload.tipo_show],
        ajuste_urgencia_percentual=financas["ajuste_urgencia_percentual"],
        cache_ajustado=financas["cache_ajustado"],
        logistica=logistica_out,
        despesas_locais=despesas_out,
        total_proposta=financas["total_proposta"],
        modelo_ticket=modelo_ticket_out,
        validade_dias_uteis=financas["validade_dias_uteis"],
        prazo_minimo_contrato_dias=financas["prazo_minimo_contrato_dias"],
        condicoes_pagamento=financas["condicoes_pagamento"],
        nota_fiscal=financas["nota_fiscal"],
    )

    if logistica["logistica_inclusa_cache"]:
        modal_desc = "Logística inclusa no cache (show de prefeitura/poder público)."
    elif logistica["modal"] == "aereo":
        modal_desc = "Logística aérea com custo a cotar conforme disponibilidade."
    else:
        modal_desc = (
            f"Logística terrestre ({logistica['distancia_km']} km) "
            f"com custo estimado de R$ {logistica['custo_ida_volta']:,.2f}."
        )

    tipo_desc = {
        "ticket": "show com venda de ingressos",
        "privado": "show privado/corporativo",
        "prefeitura": "show público (prefeitura/poder público)",
    }.get(payload.tipo_show, payload.tipo_show)

    resumo = (
        f"Para o {tipo_desc} de {nome_artista} em {payload.cidade}/{payload.estado} "
        f"no dia {payload.data_evento}, a proposta total estimada é de "
        f"R$ {financas['total_proposta']:,.2f}. "
        f"{modal_desc} "
        f"Despesas locais estimadas: R$ {dl['total_despesas_locais_estimado']:,.2f} "
        f"(responsabilidade do contratante). "
        f"Validade da proposta: {financas['validade_dias_uteis']} dias úteis. "
        f"Prazo mínimo de contrato: {financas['prazo_minimo_contrato_dias']} dias. "
        f"Condições de pagamento: {financas['condicoes_pagamento']}. "
        f"Nota fiscal inclusa."
    )

    print("  ✓ Proposta formatada com sucesso")

    return CotacaoResponse(status="aprovado", proposta=proposta, resumo_natural=resumo)


# ---------------------------------------------------------------------------
# AGENTE COORDENADOR — ENDPOINT PRINCIPAL
# ---------------------------------------------------------------------------


def _aplicar_config_do_payload(p: "CotacaoInput") -> None:
    """Aplica campos de configuração embutidos no payload de cotação ao CONFIG global."""
    if p.nome_artista is not None:
        CONFIG["nome_artista"] = p.nome_artista
    cb = CONFIG["cache_base"]
    if p.cache_ticket is not None:
        cb["ticket"] = p.cache_ticket
    if p.cache_privado is not None:
        cb["privado"] = p.cache_privado
    if p.cache_prefeitura is not None:
        cb["prefeitura"] = p.cache_prefeitura
    if p.custo_km is not None:
        CONFIG["custo_km"] = p.custo_km
    if p.limite_terrestre_km is not None:
        CONFIG["limite_terrestre_km"] = p.limite_terrestre_km
    if p.cidade_base is not None:
        CONFIG["cidade_base"] = p.cidade_base.strip()
    if p.uf_base is not None:
        CONFIG["uf_base"] = p.uf_base.strip().upper()
    dl = CONFIG["despesas_locais"]
    if p.qtd_pessoas is not None:
        dl["qtd_pessoas"] = p.qtd_pessoas
    if p.valor_diaria_por_pessoa is not None:
        dl["valor_diaria_alimentacao_por_pessoa"] = p.valor_diaria_por_pessoa
    if p.qtd_camarins is not None:
        dl["qtd_camarins_exigidos"] = p.qtd_camarins
    if p.qtd_carregadores is not None:
        dl["qtd_carregadores"] = p.qtd_carregadores
    dl["total_alimentacao"] = dl["qtd_pessoas"] * dl["valor_diaria_alimentacao_por_pessoa"] * dl["qtd_diarias"]
    dl["total_despesas_locais_estimado"] = dl["total_alimentacao"]
    mt = CONFIG["modelo_ticket"]
    if p.ticket_tipo is not None:
        mt["tipo"] = p.ticket_tipo
    if p.ticket_garantia_minima is not None:
        mt["garantia_minima"] = p.ticket_garantia_minima
    if p.ticket_percentual_porta is not None:
        mt["percentual_porta"] = p.ticket_percentual_porta
    if p.ticket_prazo_contas is not None:
        mt["prazo_prestacao_contas_horas"] = p.ticket_prazo_contas


@app.post("/cotar", response_model=CotacaoResponse, summary="Solicitar cotação de show")
async def cotar_show(payload: CotacaoInput):
    """
    Processa uma solicitação de cotação de show.
    Campos de configuração opcionais no payload sobrescrevem o CONFIG antes do cálculo.
    Fluxo: (config) → Disponibilidade → Logística → Finanças → Formatação.
    """
    _aplicar_config_do_payload(payload)
    nome = CONFIG["nome_artista"]
    print(f"\n{'='*60}")
    print(f"[COORDENADOR] {payload.tipo_show} | {payload.data_evento} | {payload.cidade}/{payload.estado}")
    print(f"  Contratante: {payload.nome_contratante} ({payload.tipo_contratante})")

    disp = agente_disponibilidade(payload.data_evento)
    if not disp["disponivel"]:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "negado_data",
                "motivo_negacao": (
                    f"A data solicitada ({payload.data_evento}) não está disponível para o artista {nome}. "
                    f"Já existe compromisso confirmado em {disp['cidade_ocupada']}. "
                    f"Sugerimos verificar as seguintes datas alternativas: {', '.join(disp['sugestoes'])}."
                ),
                "sugestoes": disp["sugestoes"],
            },
        )

    logistica = agente_logistica(
        cidade=payload.cidade,
        estado=payload.estado,
        show_dia_anterior=payload.show_dia_anterior,
        cidade_dia_anterior=payload.cidade_dia_anterior,
        show_dia_seguinte=payload.show_dia_seguinte,
        cidade_dia_seguinte=payload.cidade_dia_seguinte,
        tipo_show=payload.tipo_show,
    )

    financas = agente_financas(
        tipo_show=payload.tipo_show,
        data_evento=payload.data_evento,
        custo_logistico=logistica.get("custo_ida_volta"),
        logistica_inclusa=logistica["logistica_inclusa_cache"],
    )

    resposta = agente_formatacao(payload, logistica, financas)

    print(f"\n[COORDENADOR] ✓ Total: R$ {financas['total_proposta']:,.2f}")
    print(f"{'='*60}\n")

    return resposta


# ---------------------------------------------------------------------------
# CONFIGURAÇÃO — ENDPOINTS
# ---------------------------------------------------------------------------


@app.get("/config", summary="Obter configuração atual do sistema")
async def get_config():
    return CONFIG


@app.post("/config", summary="Atualizar configuração do sistema")
async def update_config(cfg: ConfigInput):
    """Atualiza parâmetros configuráveis em tempo de execução. Apenas campos informados são alterados."""
    if cfg.nome_artista is not None:
        CONFIG["nome_artista"] = cfg.nome_artista
    if cfg.cache_ticket is not None:
        CONFIG["cache_base"]["ticket"] = cfg.cache_ticket
    if cfg.cache_privado is not None:
        CONFIG["cache_base"]["privado"] = cfg.cache_privado
    if cfg.cache_prefeitura is not None:
        CONFIG["cache_base"]["prefeitura"] = cfg.cache_prefeitura
    if cfg.custo_km is not None:
        CONFIG["custo_km"] = cfg.custo_km
    if cfg.limite_terrestre_km is not None:
        CONFIG["limite_terrestre_km"] = cfg.limite_terrestre_km

    dl = CONFIG["despesas_locais"]
    if cfg.qtd_pessoas is not None:
        dl["qtd_pessoas"] = cfg.qtd_pessoas
    if cfg.valor_diaria_por_pessoa is not None:
        dl["valor_diaria_alimentacao_por_pessoa"] = cfg.valor_diaria_por_pessoa
    if cfg.qtd_camarins is not None:
        dl["qtd_camarins_exigidos"] = cfg.qtd_camarins
    if cfg.qtd_carregadores is not None:
        dl["qtd_carregadores"] = cfg.qtd_carregadores

    # Recalcula totais derivados
    dl["total_alimentacao"] = (
        dl["qtd_pessoas"] * dl["valor_diaria_alimentacao_por_pessoa"] * dl["qtd_diarias"]
    )
    dl["total_despesas_locais_estimado"] = dl["total_alimentacao"]

    mt = CONFIG["modelo_ticket"]
    if cfg.ticket_tipo is not None:
        if cfg.ticket_tipo not in ("garantia_porta", "fixo"):
            raise HTTPException(status_code=400, detail="ticket_tipo deve ser 'garantia_porta' ou 'fixo'")
        mt["tipo"] = cfg.ticket_tipo
    if cfg.ticket_garantia_minima is not None:
        mt["garantia_minima"] = cfg.ticket_garantia_minima
    if cfg.ticket_percentual_porta is not None:
        mt["percentual_porta"] = cfg.ticket_percentual_porta
    if cfg.ticket_prazo_contas is not None:
        mt["prazo_prestacao_contas_horas"] = cfg.ticket_prazo_contas

    if cfg.cidade_base is not None:
        CONFIG["cidade_base"] = cfg.cidade_base.strip()
    if cfg.uf_base is not None:
        uf = cfg.uf_base.strip().upper()
        if len(uf) != 2:
            raise HTTPException(status_code=400, detail="uf_base deve ter exatamente 2 letras (ex: SP, RJ)")
        CONFIG["uf_base"] = uf

    print(f"\n[CONFIG] Atualizado. Artista: {CONFIG['nome_artista']} | Cache: {CONFIG['cache_base']} | Base: {CONFIG['cidade_base']}/{CONFIG['uf_base']}")
    return CONFIG


# ---------------------------------------------------------------------------
# FRONTEND / HEALTH
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def frontend():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/health", summary="Health check")
async def health():
    return {
        "status": "online",
        "sistema": f"Cotação Shows {CONFIG['nome_artista']}",
        "versao": "1.0.0",
    }


# ---------------------------------------------------------------------------
# EXECUÇÃO DIRETA
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
