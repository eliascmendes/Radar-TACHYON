"""Normalização: telefone, bairro, tipo de site e conversão da linha do kit."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.normalizacao import (
    classificar_site,
    deduplicar,
    digitos_telefone,
    eh_celular,
    extrair_bairro,
    limpar_email,
    linha_kit_para_lead,
    normalizar_telefone,
)


def test_telefone_celular_e_fixo():
    assert normalizar_telefone("(98) 99000-0001") == "+55 98 99000-0001"
    assert normalizar_telefone("+55 98 3000-0001") == "+55 98 3000-0001"
    assert normalizar_telefone("098 3000-0001") == "+55 98 3000-0001"
    assert eh_celular("+55 98 99000-0001")
    assert not eh_celular("+55 98 3000-0001")
    assert digitos_telefone("+55 98 99000-0001") == digitos_telefone("98 99000 0001")


def test_bairro_pelo_complete_address():
    completo = '{"borough":"Cohama","street":"Av. Daniel de La Touche, 987","city":"São Luís"}'
    assert extrair_bairro("qualquer coisa", completo) == "Cohama"


def test_bairro_pelo_endereco_brasileiro():
    assert extrair_bairro("Rua das Flores, 100 - Centro, São Luís - MA, 65000-000") == "Centro"
    assert (
        extrair_bairro("Av. Colares Moreira, SALA 1115 - Jardim Renascença, São Luís - MA, 65075-770")
        == "Jardim Renascença"
    )
    assert extrair_bairro("") == ""


def test_classificar_site():
    assert classificar_site("") == ""
    assert classificar_site("https://wa.me/5598990000003") == "whatsapp"
    assert classificar_site("instagram.com/clinica") == "rede_social"
    assert classificar_site("https://contate.me/clinicaexemplo") == "agregador"
    assert classificar_site("https://linktr.ee/x") == "agregador"
    assert classificar_site("http://www.clinicaexemplo.com.br/") == "proprio"


def test_email_ignora_lixo():
    assert limpar_email("user@example.com, contato@clinica.com.br") == "contato@clinica.com.br"
    assert limpar_email("logo@2x.png") == ""


def test_linha_kit_para_lead():
    row = {
        "title": "Odonto Modelo - Calhau",
        "address": "Odonto Modelo - Calhau - Av. Litorânea, 10 - Calhau, São Luís - MA, 65000-000",
        "phone": "(98) 99000-0002",
        "website": "https://www.instagram.com/odontomodelo",
        "review_count": "169",
        "review_rating": "4.9",
        "complete_address": '{"borough":"Calhau"}',
        "reservations": "null",
        "owner": '{"id":"1","name":"x"}',
        "latitude": "-2.49",
        "longitude": "-44.25",
        "place_id": "abc",
    }
    lead = linha_kit_para_lead(row, "clinicas", 1)
    assert lead["endereco"].startswith("Av. Litorânea")
    assert lead["bairro"] == "Calhau"
    assert lead["tipo_site"] == "rede_social"
    assert lead["instagram"].startswith("https://www.instagram.com/")
    assert lead["sinais_maps"]["perfil_reivindicado"]
    assert not lead["sinais_maps"]["reserva_pelo_google"]
    assert lead["avaliacoes"] == 169 and lead["nota"] == 4.9


def test_dedupe_por_place_id_e_cid():
    rows = [
        {"place_id": "a", "title": "X"},
        {"place_id": "a", "title": "X filial"},
        {"cid": "9", "title": "Y"},
        {"cid": "9", "title": "Y"},
        {"title": "Z", "address": "Rua 1"},
    ]
    assert len(deduplicar(rows)) == 3
