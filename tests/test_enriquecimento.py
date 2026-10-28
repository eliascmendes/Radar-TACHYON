"""Enriquecimento com respostas HTTP simuladas (sem rede)."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.enriquecimento import analisar_site, enriquecer_lead

MODERNO = """<html><head><meta name="viewport" content="width=device-width"></head>
<body><a href="https://wa.me/5598999999999">WhatsApp</a>
<a href="https://instagram.com/clinica.exemplo">IG</a> <a href="mailto:contato@clinica.com.br">mail</a>
Agende online sua consulta. Área do paciente. <a href="https://apps.apple.com/app/x">App</a>
© 2026 Clínica</body></html>"""

ANTIGO = "<html><body><table><tr><td>Bem-vindo</td></tr></table>Copyright © 2012</body></html>"


def cliente(rotas: dict[str, tuple[int, str]]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        status, corpo = rotas.get(str(req.url), (404, "nao achou"))
        return httpx.Response(status, text=corpo, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_site_moderno():
    r = analisar_site("https://clinica.com.br/", client=cliente({"https://clinica.com.br/": (200, MODERNO)}))
    assert r["analisou_site"] and not r["site_quebrado"]
    assert not r["sem_mobile"] and r["tem_whatsapp"] and r["tem_agendamento_online"]
    assert r["redes_encontradas"]["instagram"] == "https://instagram.com/clinica.exemplo"
    assert r["email_encontrado"] == "contato@clinica.com.br"
    assert r["sistema_proprio_maduro"]


def test_site_antigo_sem_mobile():
    r = analisar_site("https://velho.com.br/", client=cliente({"https://velho.com.br/": (200, ANTIGO)}))
    assert r["sem_mobile"] and r["parece_desatualizado"] and r["ano_rodape"] == 2012
    assert not r["tem_whatsapp"]


def test_403_e_bloqueio_nao_quebrado():
    r = analisar_site("https://bloq.com.br/", client=cliente({"https://bloq.com.br/": (403, "forbidden")}))
    assert r["bloqueou_verificacao"] and not r["site_quebrado"]


def test_subpagina_404_tenta_raiz():
    rotas = {"https://x.com.br/home/": (404, ""), "https://x.com.br/": (200, MODERNO)}
    r = analisar_site("https://x.com.br/home/", client=cliente(rotas))
    assert r["analisou_site"] and not r["site_quebrado"]


def test_500_e_quebrado():
    r = analisar_site("https://caiu.com.br/", client=cliente({"https://caiu.com.br/": (500, "erro")}))
    assert r["site_quebrado"]


def test_link_whatsapp_nao_e_site():
    r = enriquecer_lead({"site": "https://wa.me/5598990000003", "telefone": "+55 98 99000-0003"})
    assert r["sem_site"] and r["tem_whatsapp"] and r["whatsapp_provavel"]


def test_reserva_google_conta_como_agendamento():
    r = enriquecer_lead({"site": "", "telefone": "", "sinais_maps": {"reserva_pelo_google": True}})
    assert r["tem_agendamento_online"] and not r["sem_agendamento_online"]


def test_doctoralia_e_gov():
    r = enriquecer_lead({"site": "https://www.doctoralia.com.br/dr-x", "telefone": ""})
    assert r["sem_site"] and r["tem_agendamento_online"]
    r = enriquecer_lead({"site": "https://www.ma.gov.br/servicos/sorrir", "telefone": ""})
    assert r["orgao_publico"]
