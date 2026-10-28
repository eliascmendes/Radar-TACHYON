"""Mensagens de abordagem.

Escritas para fazer sentido sem revisão, inclusive quando a mesma campanha vai para
várias empresas: só citam problemas que a verificação confirmou e nunca dependem de
quem vai assinar. O envio é sempre aberto e confirmado por uma pessoa no WhatsApp.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.config_loader import mensagens as carregar_mensagens
from app.config_loader import responsaveis

# O Maps costuma juntar nome e descrição: "Sorriso e Cia: Dentista", "Odonto X | Shopping".
_SEPARADORES_NOME = re.compile(r"\s+[|–—-]\s+|:\s+")


def nome_curto(nome: str | None) -> str:
    nome = re.sub(r"\s+", " ", (nome or "").strip())
    curto = _SEPARADORES_NOME.split(nome, maxsplit=1)[0].strip()
    return curto if len(curto) >= 3 else nome


def observacao(enriq: dict | None) -> str:
    """Um problema confirmado da presença digital, ou '' se não houver nada seguro para citar."""
    e = enriq or {}
    if e.get("orgao_publico"):
        return ""
    # Análises antigas marcavam 403/429 como site quebrado; na prática é bloqueio de robô.
    bloqueado = e.get("bloqueou_verificacao") or e.get("http_status") in (401, 403, 429)
    if e.get("site_quebrado") and not bloqueado:
        return "o site não abriu quando tentamos acessar"
    if e.get("sem_site") and e.get("so_rede_social"):
        return "vocês aparecem nas redes sociais, mas ainda não têm um site próprio"
    if e.get("sem_site"):
        return "vocês ainda não têm um site próprio"
    if bloqueado:
        return ""
    if e.get("sem_mobile"):
        return "o site não se adapta bem à tela do celular"
    if e.get("parece_desatualizado"):
        return "o site parece estar há algum tempo sem atualização"
    if e.get("sem_agendamento_online") and e.get("analisou_site"):
        return "o site não oferece uma forma de agendar ou pedir online"
    return ""


def saudacao(agora: datetime | None = None) -> str:
    hora = (agora or datetime.now()).hour
    if 5 <= hora < 12:
        return "Bom dia"
    if 12 <= hora < 18:
        return "Boa tarde"
    return "Boa noite"


def gerar_rascunho(
    *,
    nome: str,
    segmento_id: str | None,
    enriquecimento: dict | None = None,
    responsavel: str | None = None,
    agora: datetime | None = None,
) -> str:
    cfg = carregar_mensagens()
    seg = {**cfg.get("padrao", {}), **cfg.get("segmentos", {}).get(segmento_id or "", {})}
    equipe = cfg.get("equipe", "TACHYON")
    empresa = nome_curto(nome) or "sua empresa"
    obs = observacao(enriquecimento)

    campos = {
        "empresa": empresa,
        "publico": seg.get("publico", "empresas"),
        "observacao": obs,
    }
    abertura_modelo = cfg.get("abertura_com_observacao") if obs else cfg.get("abertura_sem_observacao")
    campos.update(
        saudacao=saudacao(agora),
        assinatura=f"{responsavel}, da {equipe}" if responsavel in responsaveis() else f"a {equipe}",
        apresentacao=cfg.get("apresentacao", ""),
        abertura=(abertura_modelo or "").format(**campos),
        proposta=seg.get("proposta", ""),
        convite=seg.get("convite", ""),
        saida=cfg.get("saida", ""),
    )
    texto = seg.get("modelo") or cfg.get("modelo", "")
    return re.sub(r"\n{3,}", "\n\n", texto.format(**campos)).strip()
