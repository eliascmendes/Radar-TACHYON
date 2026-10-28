"""Pontuação 0-100 com pesos em config/pesos.json e motivos em texto."""
from __future__ import annotations

from app.config_loader import pesos as carregar_pesos
from app.config_loader import segmento_por_id


def _clamp(n: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, n))


def _score_porte(avaliacoes: int, nota: float | None, cfg: dict) -> tuple[float, list[str]]:
    p = cfg.get("porte", {})
    motivos: list[str] = []
    if avaliacoes <= 20:
        base = float(p.get("reviews_ate_20", 10))
        motivos.append(f"{avaliacoes} avaliações (porte baixo)")
    elif avaliacoes <= 100:
        base = float(p.get("reviews_21_100", 40))
        motivos.append(f"{avaliacoes} avaliações")
    elif avaliacoes <= 300:
        base = float(p.get("reviews_101_300", 70))
        motivos.append(f"{avaliacoes} avaliações (porte médio-alto)")
    else:
        base = float(p.get("reviews_acima_300", 100))
        motivos.append(f"{avaliacoes} avaliações (alto volume)")

    bonus = 0.0
    if nota is not None and nota > 3.0:
        # +5 por cada 0,5 acima de 3.0
        passos = (nota - 3.0) / 0.5
        bonus = min(float(p.get("nota_bonus_teto", 20)), passos * float(p.get("nota_bonus_por_ponto_acima_de_3", 5)))
    if nota is not None:
        motivos.append(f"nota {nota}")

    return _clamp(base + bonus), motivos


def _score_necessidade(enriq: dict, cfg: dict) -> tuple[float, list[str]]:
    n = cfg.get("necessidade", {})
    pontos = 0.0
    motivos: list[str] = []

    def soma(chave: str, padrao: float, motivo: str) -> None:
        nonlocal pontos
        pontos += float(n.get(chave, padrao))
        motivos.append(motivo)

    if enriq.get("so_rede_social"):
        soma("so_rede_social", 35, "presença só em rede social")
    if enriq.get("sem_site"):
        soma("sem_site", 50, "sem site")
    if enriq.get("site_quebrado"):
        soma("site_quebrado", 45, "site quebrado")
    if enriq.get("certificado_invalido"):
        soma("sem_https", 15, "certificado HTTPS inválido")
    elif enriq.get("sem_https"):
        soma("sem_https", 15, "sem HTTPS")
    if enriq.get("sem_mobile"):
        soma("sem_mobile", 15, "sem versão mobile")
    if enriq.get("parece_desatualizado"):
        ano = enriq.get("ano_rodape")
        soma("parece_desatualizado", 20, f"site parece desatualizado (rodapé {ano})" if ano else "site parece desatualizado")
    if enriq.get("sem_agendamento_online") and not enriq.get("tem_agendamento_online"):
        soma("sem_agendamento_online", 15, "sem agendamento online")
    # só faz sentido falar em botão quando o site foi lido
    if enriq.get("analisou_site") and not enriq.get("tem_whatsapp"):
        soma("sem_whatsapp", 10, "sem botão de WhatsApp no site")
    if enriq.get("lento"):
        soma("lento", 10, "site lento")

    if enriq.get("sem_site") and not enriq.get("tem_agendamento_online") and (
        enriq.get("whatsapp_provavel") or enriq.get("tem_whatsapp")
    ):
        motivos.append("agenda só por WhatsApp")
    if enriq.get("bloqueou_verificacao"):
        motivos.append("site bloqueou a verificação automática (conferir à mão)")

    return _clamp(pontos), motivos


# Usado quando o segmento não define "palavras_categoria" em segmentos.json.
PALAVRAS_PADRAO = {
    "clinicas": ["odont", "dentist", "clínic", "clinic", "consult", "salão", "salao", "estét", "estet", "lab"],
    "transporte": ["ônibus", "onibus", "transporte", "fretamento", "van"],
    "servicos_engenharia": ["engenharia", "manutenção", "manutencao", "reforma", "serviço", "servico"],
    "educacao": ["escola", "curso", "idioma", "preparat"],
    "alimentacao": ["restaur", "lanch", "delivery", "comida"],
    "imobiliario": ["imob", "construt", "corretor"],
    "logistica_porto": ["logíst", "logist", "porto", "indústria", "industria", "transportadora"],
}


def _score_aderencia(segmento_id: str | None, categoria: str | None, cfg_seg: dict | None) -> tuple[float, list[str]]:
    if not segmento_id:
        return 40.0, ["segmento não classificado"]
    prioridade = (cfg_seg or {}).get("prioridade", "media")
    mapa = {"alta": 95.0, "media": 65.0, "baixa": 35.0}
    base = mapa.get(prioridade, 50.0)
    motivos = [f"segmento {(cfg_seg or {}).get('nome', segmento_id)} ({prioridade})"]
    cat = (categoria or "").lower()
    palavras = (cfg_seg or {}).get("palavras_categoria") or PALAVRAS_PADRAO.get(segmento_id, [])
    for p in palavras:
        if p.lower() in cat:
            base = min(100.0, base + 5)
            motivos.append(f"categoria Maps alinhada ({categoria})")
            break
    return base, motivos


def _penalidade_maduro(enriq: dict, cfg: dict) -> tuple[float, list[str]]:
    if enriq.get("orgao_publico"):
        return 100.0, ["órgão público (fora do público-alvo)"]
    if enriq.get("sistema_proprio_maduro"):
        pen = float(cfg.get("penalidade_sistema_maduro", 35))
        return pen, ["já mostra sistema próprio maduro"]
    return 0.0, []


def pontuar_detalhado(
    *,
    avaliacoes: int = 0,
    nota: float | None = None,
    segmento_id: str | None = None,
    categoria: str | None = None,
    enriquecimento: dict | None = None,
    cfg: dict | None = None,
) -> dict:
    """Pontuação com a contribuição de cada componente (para o painel)."""
    cfg = cfg or carregar_pesos()
    enriq = enriquecimento or {}
    pesos_base = cfg.get("pesos_base", {})
    w_porte = float(pesos_base.get("porte_pagamento", 30))
    w_nec = float(pesos_base.get("necessidade_digital", 40))
    w_ader = float(pesos_base.get("aderencia_segmento", 30))

    s_porte, m_porte = _score_porte(int(avaliacoes or 0), nota, cfg)
    s_nec, m_nec = _score_necessidade(enriq, cfg)
    seg = segmento_por_id(segmento_id) if segmento_id else None
    s_ader, m_ader = _score_aderencia(segmento_id, categoria, seg)
    pen, m_pen = _penalidade_maduro(enriq, cfg)

    c_porte = s_porte * w_porte / 100.0
    c_nec = s_nec * w_nec / 100.0
    c_ader = s_ader * w_ader / 100.0
    score = int(round(_clamp(c_porte + c_nec + c_ader - pen)))

    motivos = m_porte + m_nec + m_ader + m_pen
    return {
        "pontuacao": score,
        "motivos": "; ".join(motivos) if motivos else "sem sinais relevantes",
        "componentes": {
            "porte": {"nota": round(s_porte), "peso": w_porte, "pontos": round(c_porte, 1)},
            "necessidade": {"nota": round(s_nec), "peso": w_nec, "pontos": round(c_nec, 1)},
            "aderencia": {"nota": round(s_ader), "peso": w_ader, "pontos": round(c_ader, 1)},
            "penalidade": round(pen, 1),
        },
    }


def pontuar(**kwargs) -> tuple[int, str]:
    """Retorna (pontuação 0-100, motivos concatenados). Pesos vêm de config/pesos.json."""
    d = pontuar_detalhado(**kwargs)
    return d["pontuacao"], d["motivos"]
