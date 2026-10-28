"""API e pipeline ponta a ponta com banco temporário (sem Docker, sem rede)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, pipeline

LINHAS_KIT = [
    {
        "place_id": "p1",
        "title": "Clínica Exemplo",
        "address": "Clínica Exemplo - Rua das Flores, 100 - Centro, São Luís - MA, 65000-000",
        "phone": "(98) 99000-0001",
        "website": "",
        "category": "Clínica odontológica",
        "review_count": "349",
        "review_rating": "4.9",
        "complete_address": '{"borough":"Centro"}',
    },
    {
        "place_id": "p2",
        "title": "Odonto Digital",
        "address": "Odonto Digital - Rua do Sol, 200 - Centro, São Luís - MA, 65000-000",
        "phone": "(98) 3000-0001",
        "website": "https://odontodigital.exemplo.com.br",
        "category": "Dentista",
        "review_count": "12",
        "review_rating": "4.0",
    },
    {"place_id": "p3", "title": "Fechada Ltda", "status": "Fechado permanentemente", "review_count": "5"},
]


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "radar.db")
    monkeypatch.setattr(pipeline, "EXPORTS", tmp_path / "exports")
    monkeypatch.setattr(pipeline, "BRUTOS", tmp_path / "brutos")
    monkeypatch.setattr(pipeline, "executar_coleta", lambda *a, **k: (list(LINHAS_KIT), {"job_ids": ["j1"]}))

    def enriquecer_falso(leads, **_):
        from app.enriquecimento import _base, enriquecer_lead

        saida = []
        for L in leads:
            if L["site"]:
                r = _base()
                r.update(analisou_site=True, tem_agendamento_online=True, sem_agendamento_online=False)
                saida.append(r)
            else:
                saida.append(enriquecer_lead(L))
        return saida

    monkeypatch.setattr(pipeline, "enriquecer_lote", enriquecer_falso)
    from app.main import app

    with TestClient(app) as c:
        pipeline.rodar_campanha(nome="teste", termos=["clínica odontológica"], segmento_id="clinicas", log=lambda m: None)
        yield c


def test_pipeline_grava_leads_ordenados(cliente):
    data = cliente.get("/api/leads").json()
    assert data["total_db"] == 2  # a fechada foi descartada
    primeiro = data["itens"][0]
    assert primeiro["nome"] == "Clínica Exemplo"
    assert primeiro["bairro"] == "Centro"
    assert primeiro["status"] == "qualificado"
    assert primeiro["whatsapp"] == "https://wa.me/5598990000001"
    assert data["itens"][1]["whatsapp"] == ""  # fixo


def test_filtros_e_resumo(cliente):
    assert cliente.get("/api/leads", params={"sem_site": True}).json()["total_filtrado"] == 1
    assert cliente.get("/api/leads", params={"q": "digital"}).json()["total_filtrado"] == 1
    r = cliente.get("/api/resumo").json()
    assert r["total"] == 2 and r["qualificados"] >= 1


def test_ficha_patch_e_historico(cliente, monkeypatch):
    monkeypatch.setattr("app.main.responsaveis", lambda: ["Ana"])  # independe do equipe.json local
    lid = cliente.get("/api/leads").json()["itens"][0]["id"]
    r = cliente.patch(f"/api/leads/{lid}", json={"status": "contatado", "quem": "Ana"})
    assert r.status_code == 200 and r.json()["status"] == "contatado"
    ficha = cliente.get(f"/api/leads/{lid}").json()
    assert ficha["componentes"]["porte"]["pontos"] > 0
    assert any(h["campo"] == "status" and h["quem"] == "Ana" for h in ficha["historico"])
    assert cliente.patch(f"/api/leads/{lid}", json={"status": "xyz"}).status_code == 400


def test_recoleta_preserva_status_manual(cliente):
    lid = cliente.get("/api/leads").json()["itens"][0]["id"]
    cliente.patch(f"/api/leads/{lid}", json={"status": "reuniao"})
    pipeline.rodar_campanha(nome="de novo", termos=["clínica"], segmento_id="clinicas", log=lambda m: None)
    assert cliente.get(f"/api/leads/{lid}").json()["status"] == "reuniao"
    assert cliente.get("/api/leads").json()["total_db"] == 2


def test_rascunho_e_nao_contatar(cliente):
    lid = cliente.get("/api/leads").json()["itens"][0]["id"]
    r = cliente.post(f"/api/leads/{lid}/rascunho").json()
    assert "TACHYON" in r["rascunho"] and "confirmar" in r["aviso"]
    # telefone digitado em outro formato também bloqueia
    r = cliente.post("/api/nao-contatar", json={"telefone": "98 99000 0001", "motivo": "pediu"}).json()
    assert r["leads_descartados"] == 1
    assert cliente.post(f"/api/leads/{lid}/rascunho").status_code == 403
    assert cliente.patch(f"/api/leads/{lid}", json={"status": "contatado"}).status_code == 403


def test_funil_repontuar_exportar_excluir(cliente):
    f = cliente.get("/api/funil").json()
    assert [s["status"] for s in f["por_status"]][0] == "novo"
    assert f["taxa_por_segmento"][0]["segmento"].startswith("Clínicas")
    assert cliente.post("/api/leads/repontuar").json()["total"] == 2
    csv = cliente.get("/api/exportar").text
    assert csv.splitlines()[0].lstrip("﻿").startswith("pontuacao,motivos,nome")
    lid = cliente.get("/api/leads").json()["itens"][0]["id"]
    assert cliente.delete(f"/api/leads/{lid}").status_code == 200
    assert cliente.get(f"/api/leads/{lid}").status_code == 404


def test_reprocessar_usa_csv_bruto(cliente):
    res = pipeline.reprocessar_campanha(1, log=lambda m: None)
    assert res["total"] == 2 and res["novos"] == 0 and res["atualizados"] == 2


def _ids(cliente):
    return {L["nome"]: L["id"] for L in cliente.get("/api/leads").json()["itens"]}


def test_fila_e_envio_whatsapp(cliente):
    ids = _ids(cliente)
    fila = cliente.post("/api/envios/previa", json={}).json()
    assert [i["nome"] for i in fila["itens"]] == ["Clínica Exemplo"]
    assert "TACHYON" in fila["itens"][0]["mensagem"] and "{" not in fila["itens"][0]["mensagem"]
    assert fila["resumo_exclusao"] == {"sem celular para WhatsApp": 1}

    lid = ids["Clínica Exemplo"]
    r = cliente.post(f"/api/leads/{lid}/whatsapp", json={"texto": "Olá!\nTudo bem?", "quem": "ninguém"}).json()
    assert r["url"] == "https://wa.me/5598990000001?text=Ol%C3%A1%21%0ATudo%20bem%3F"
    assert r["enviados_hoje"] == 1
    ficha = cliente.get(f"/api/leads/{lid}").json()
    assert ficha["status"] == "contatado" and ficha["rascunho"] == "Olá!\nTudo bem?"
    assert any(h["campo"] == "whatsapp" for h in ficha["historico"])

    # quem já recebeu sai da próxima fila, a menos que se peça para incluir
    fila = cliente.post("/api/envios/previa", json={"status": None}).json()
    assert fila["itens"] == [] and fila["resumo_exclusao"]["já contatado"] == 1
    fila = cliente.post("/api/envios/previa", json={"status": None, "incluir_contatados": True}).json()
    assert [i["id"] for i in fila["itens"]] == [lid]

    # sem celular e lista não contatar bloqueiam também no registro
    assert cliente.post(f"/api/leads/{ids['Odonto Digital']}/whatsapp", json={"texto": "oi"}).status_code == 400
    cliente.post("/api/nao-contatar", json={"lead_id": lid, "motivo": "pediu"})
    assert cliente.post(f"/api/leads/{lid}/whatsapp", json={"texto": "oi"}).status_code == 403
    fila = cliente.post("/api/envios/previa", json={"status": None, "incluir_contatados": True}).json()
    assert fila["itens"] == []


def test_limite_diario_de_envios(cliente, monkeypatch):
    monkeypatch.setattr("app.main._limite_diario", lambda: 1)
    lid = _ids(cliente)["Clínica Exemplo"]
    assert cliente.post(f"/api/leads/{lid}/whatsapp", json={"texto": "oi"}).status_code == 200
    r = cliente.post(f"/api/leads/{lid}/whatsapp", json={"texto": "oi de novo"})
    assert r.status_code == 429 and "Limite" in r.json()["detail"]
