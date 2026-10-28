"""Mensagens de abordagem: precisam fazer sentido para qualquer empresa da fila, sem revisão."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rascunhos import gerar_rascunho, nome_curto, observacao, saudacao

TARDE = datetime(2026, 10, 27, 15, 0)


def test_nome_curto_tira_descricao_do_maps():
    assert nome_curto("Sorriso e Cia: Dentista") == "Sorriso e Cia"
    assert nome_curto("Odonto Modelo | Shopping da Ilha") == "Odonto Modelo"
    assert nome_curto("Odonto Modelo - Calhau") == "Odonto Modelo"
    assert nome_curto("Auto-Escola Exemplo") == "Auto-Escola Exemplo"
    assert nome_curto("  ") == ""


def test_saudacao_pelo_horario():
    assert saudacao(datetime(2026, 1, 1, 8)) == "Bom dia"
    assert saudacao(datetime(2026, 1, 1, 14)) == "Boa tarde"
    assert saudacao(datetime(2026, 1, 1, 20)) == "Boa noite"


def test_so_cita_problema_confirmado():
    assert "não abriu" in observacao({"site_quebrado": True})
    # verificação bloqueada pelo site: nada é afirmado
    assert observacao({"site_quebrado": True, "bloqueou_verificacao": True}) == ""
    assert observacao({"bloqueou_verificacao": True, "sem_mobile": True}) == ""
    assert observacao({"site_quebrado": True, "http_status": 403}) == ""
    assert observacao({"orgao_publico": True, "sem_site": True}) == ""
    # sem agendamento só conta se o site foi de fato lido
    assert observacao({"sem_agendamento_online": True}) == ""
    assert "agendar" in observacao({"sem_agendamento_online": True, "analisou_site": True})
    assert "redes sociais" in observacao({"sem_site": True, "so_rede_social": True})


def test_mensagem_com_problema():
    t = gerar_rascunho(nome="Sorriso e Cia: Dentista", segmento_id="clinicas", enriquecimento={"sem_site": True}, agora=TARDE)
    assert t.startswith("Boa tarde! Aqui é a TACHYON")
    assert "Vimos o perfil de vocês no Google Maps e notamos que vocês ainda não têm um site próprio." in t
    assert "agendamento online" in t
    assert "responder \"sair\"" in t
    assert "Dentista" not in t and "{" not in t


def test_mensagem_sem_problema_nao_inventa():
    t = gerar_rascunho(nome="Clínica Exemplo", segmento_id="clinicas", enriquecimento={}, agora=TARDE)
    assert "notamos" not in t
    assert "clínicas e consultórios da região" in t


def test_assinatura_e_segmento_desconhecido(monkeypatch):
    monkeypatch.setattr("app.rascunhos.responsaveis", lambda: ["Ana"])
    t = gerar_rascunho(nome="Loja X", segmento_id="inexistente", responsavel="Ana", agora=TARDE)
    assert "Aqui é Ana, da TACHYON" in t
    assert "empresas da região" in t
    # nome fora da equipe não assina
    assert "Fulano" not in gerar_rascunho(nome="Loja X", segmento_id=None, responsavel="Fulano", agora=TARDE)
