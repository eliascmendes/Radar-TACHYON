"""Pontuação: casos típicos de leads com e sem presença digital."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.pontuacao import pontuar


def test_sem_site_muitas_avaliacoes():
    score, motivos = pontuar(
        avaliacoes=120,
        nota=4.5,
        segmento_id="clinicas",
        categoria="Clínica odontológica",
        enriquecimento={
            "sem_site": True,
            "so_rede_social": False,
            "sem_agendamento_online": True,
            "sem_whatsapp": True,
            "whatsapp_provavel": True,
            "analisou_site": False,
        },
    )
    assert score >= 55, score
    assert "120 avaliações" in motivos
    assert "sem site" in motivos
    assert "agenda só por WhatsApp" in motivos


def test_site_moderno_sistema_maduro():
    score, motivos = pontuar(
        avaliacoes=80,
        nota=4.8,
        segmento_id="clinicas",
        categoria="Dentista",
        enriquecimento={
            "sem_site": False,
            "site_quebrado": False,
            "sem_https": False,
            "sem_mobile": False,
            "parece_desatualizado": False,
            "sem_agendamento_online": False,
            "tem_agendamento_online": True,
            "tem_area_cliente": True,
            "tem_whatsapp": True,
            "sem_whatsapp": False,
            "sistema_proprio_maduro": True,
            "analisou_site": True,
        },
    )
    assert score < 55, score
    assert "sistema próprio maduro" in motivos


def test_site_quebrado():
    score, motivos = pontuar(
        avaliacoes=40,
        nota=4.0,
        segmento_id="clinicas",
        categoria="Consultório",
        enriquecimento={
            "sem_site": False,
            "site_quebrado": True,
            "sem_https": True,
            "sem_mobile": True,
            "sem_agendamento_online": True,
            "analisou_site": True,
            "sem_whatsapp": True,
        },
    )
    assert score >= 50, score
    assert "site quebrado" in motivos


def test_so_instagram():
    score, motivos = pontuar(
        avaliacoes=25,
        nota=4.2,
        segmento_id="clinicas",
        categoria="Estética",
        enriquecimento={
            "sem_site": True,
            "so_rede_social": True,
            "sem_agendamento_online": True,
            "sem_whatsapp": True,
        },
    )
    assert score >= 50, score
    assert "presença só em rede social" in motivos
    assert "sem site" in motivos


def test_transporte_alta_aderencia():
    score, motivos = pontuar(
        avaliacoes=200,
        nota=4.0,
        segmento_id="transporte",
        categoria="Fretamento de ônibus",
        enriquecimento={"sem_site": True, "sem_agendamento_online": True},
    )
    assert score >= 60, score
    assert "transporte" in motivos.lower() or "passagens" in motivos.lower() or "alta" in motivos


def test_faixa_0_100():
    score, _ = pontuar(avaliacoes=0, nota=1.0, segmento_id="logistica_porto", enriquecimento={})
    assert 0 <= score <= 100
    score2, _ = pontuar(
        avaliacoes=9999,
        nota=5.0,
        segmento_id="transporte",
        enriquecimento={
            "sem_site": True,
            "site_quebrado": True,
            "sem_https": True,
            "sem_mobile": True,
            "parece_desatualizado": True,
            "sem_agendamento_online": True,
            "so_rede_social": True,
            "lento": True,
            "sem_whatsapp": True,
            "analisou_site": False,
        },
    )
    assert 0 <= score2 <= 100


def test_fixo_sem_site_nao_afirma_whatsapp():
    _, motivos = pontuar(
        avaliacoes=50,
        segmento_id="clinicas",
        enriquecimento={"sem_site": True, "sem_agendamento_online": True, "whatsapp_provavel": False},
    )
    assert "agenda só por WhatsApp" not in motivos
    assert "botão de WhatsApp" not in motivos


def test_bloqueio_de_robo_nao_conta_como_quebrado():
    base = dict(avaliacoes=80, nota=4.6, segmento_id="clinicas", categoria="Dentista")
    bloqueado, motivos = pontuar(**base, enriquecimento={"bloqueou_verificacao": True, "sem_agendamento_online": True})
    quebrado, _ = pontuar(**base, enriquecimento={"site_quebrado": True, "sem_agendamento_online": True})
    assert bloqueado < quebrado
    assert "site quebrado" not in motivos
    assert "conferir" in motivos


def test_componentes_somam_a_pontuacao():
    from app.pontuacao import pontuar_detalhado

    d = pontuar_detalhado(
        avaliacoes=150, nota=4.7, segmento_id="transporte", categoria="Fretamento",
        enriquecimento={"sem_site": True, "sem_agendamento_online": True},
    )
    c = d["componentes"]
    soma = c["porte"]["pontos"] + c["necessidade"]["pontos"] + c["aderencia"]["pontos"] - c["penalidade"]
    assert abs(soma - d["pontuacao"]) <= 1
