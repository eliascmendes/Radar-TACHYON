"""Enriquecimento de presença digital. Falha em um site não interrompe a campanha.

Diferença importante: "site quebrado" só quando o site realmente não responde
(DNS, conexão, 5xx, 404 na raiz). Bloqueio a robô (403/429) e certificado
inválido viram sinais próprios, para não inflar a pontuação por engano.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import httpx

from app.normalizacao import DOMINIOS_AGENDAMENTO, classificar_site, eh_celular, limpar_email

# Navegador comum: muitos sites (Cloudflare, Wix) devolvem 403 para UA de robô.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "pt-BR,pt;q=0.9"}

SINAIS_AGENDAMENTO = re.compile(
    r"agende\s+(?:já|agora|online|sua)|agendar\s+(?:consulta|online|horário|horario|agora)|"
    r"agendamento\s+online|marque\s+(?:sua\s+)?consulta|book\s+online|calendly\.com|"
    r"doctoralia|clinicorp|feegow|iclinic|simples\s*dental|agendasalao|trinks\.com|"
    r"booksy|avec\.app|reservar\s+online",
    re.I,
)
SINAIS_LOJA = re.compile(r"carrinho|add[\s_-]to[\s_-]cart|woocommerce|cdn\.shopify|nuvemshop|loja\s+virtual|finalizar\s+compra", re.I)
SINAIS_AREA_CLIENTE = re.compile(
    r"área\s+do\s+(?:cliente|paciente|aluno)|area\s+do\s+(?:cliente|paciente|aluno)|"
    r"portal\s+do\s+(?:cliente|paciente|aluno)|minha\s+conta",
    re.I,
)
SINAIS_APP = re.compile(r"apps\.apple\.com|play\.google\.com/store/apps", re.I)
SINAIS_WHATSAPP = re.compile(r"wa\.me/|api\.whatsapp\.com|whatsapp\.com/send|web\.whatsapp", re.I)
VIEWPORT = re.compile(r"<meta[^>]+name=[\"']viewport[\"']", re.I)
ANO_RODAPE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", re.I)
SINAIS_LEGADO = re.compile(r"frontpage|macromedia|dreamweaver|\.swf[\"']|<frameset|<marquee", re.I)
MAILTO = re.compile(r"mailto:([^\"'>\s?]+)", re.I)

SOCIAL_RE = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)", re.I),
    "facebook": re.compile(r"https?://(?:www\.|m\.|web\.|pt-br\.)?facebook\.com/([A-Za-z0-9_.\-]+)", re.I),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/([A-Za-z0-9_.\-%]+)", re.I),
}
_HANDLE_LIXO = {
    "", "home", "pages", "people", "help", "about", "policies", "privacy", "sharer", "tr",
    "profile.php", "plugins", "dialog", "intent", "login", "share.php", "p", "reel", "reels",
    "explore", "stories", "tv", "watch", "events", "groups", "hashtag", "search", "wix",
}

PLATAFORMAS = [
    ("WordPress", re.compile(r"wp-content|wp-includes", re.I)),
    ("Wix", re.compile(r"wixstatic|wix\.com", re.I)),
    ("Squarespace", re.compile(r"squarespace", re.I)),
    ("Shopify", re.compile(r"cdn\.shopify", re.I)),
    ("Nuvemshop", re.compile(r"nuvemshop|tiendanube", re.I)),
    ("Webflow", re.compile(r"webflow", re.I)),
    ("Next.js", re.compile(r"/_next/|__NEXT_DATA__", re.I)),
    ("Google Sites", re.compile(r"sites\.google\.com", re.I)),
]

LIMITE_LENTO_MS = 4000
ANOS_DESATUALIZADO = 4


def _base(lead: dict | None = None) -> dict:
    return {
        "tipo_site": "",
        "sem_site": False,
        "site_quebrado": False,
        "bloqueou_verificacao": False,
        "certificado_invalido": False,
        "sem_https": False,
        "sem_mobile": False,
        "parece_desatualizado": False,
        "ano_rodape": None,
        "sem_agendamento_online": True,
        "tem_agendamento_online": False,
        "tem_loja": False,
        "tem_area_cliente": False,
        "tem_app": False,
        "tem_whatsapp": False,
        "sem_whatsapp": True,
        "whatsapp_provavel": False,
        "lento": False,
        "plataforma": "",
        "so_rede_social": False,
        "sistema_proprio_maduro": False,
        "analisou_site": False,
        "http_status": None,
        "tempo_ms": None,
        "url_final": "",
        "erro": "",
        "redes_encontradas": {},
        "email_encontrado": "",
    }


def _url_completa(site: str) -> str:
    site = (site or "").strip()
    if not site:
        return ""
    if not site.startswith(("http://", "https://")):
        return "https://" + site
    return site


def _raiz(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def _tentar(url: str, timeout: float, client: httpx.Client | None = None) -> tuple[httpx.Response | None, str, bool]:
    """Tenta a URL com algumas saídas antes de declarar o site quebrado:
    timeout → tenta de novo; erro de certificado → tenta sem verificação;
    HTTPS impossível → tenta http://.

    Retorna (resposta, erro, certificado_invalido).
    """
    proprio = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=timeout, headers=HEADERS)
    cert_ruim = False
    try:
        try:
            return client.get(url), "", False
        except httpx.TimeoutException as e:
            msg = f"timeout ({e.__class__.__name__})"
            try:
                return client.get(url, timeout=timeout * 1.5), "", False
            except Exception:
                pass
        except httpx.ConnectError as e:
            msg = str(e)
            if "SSL" in msg or "certificate" in msg.lower() or "TLS" in msg:
                cert_ruim = True
                with httpx.Client(follow_redirects=True, timeout=timeout, headers=HEADERS, verify=False) as inseguro:
                    try:
                        return inseguro.get(url), "", True
                    except Exception as e2:
                        msg = str(e2)
        except Exception as e:
            return None, str(e)[:200], False
        if url.startswith("https://"):
            try:
                return client.get("http://" + url[len("https://") :]), "", cert_ruim
            except Exception:
                pass
        return None, msg[:200], cert_ruim
    finally:
        if proprio:
            client.close()


def _redes(html: str) -> dict:
    achou: dict[str, str] = {}
    for plat, rx in SOCIAL_RE.items():
        for m in rx.finditer(html or ""):
            h = m.group(1).lower()
            if h in _HANDLE_LIXO or (plat == "facebook" and (h.isdigit() or len(h) < 3)):
                continue
            achou[plat] = m.group(0).rstrip("\"'/").replace("\\", "")
            break
    return achou


def _email(html: str, url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    candidatos = [limpar_email(m) for m in MAILTO.findall(html or "")]
    candidatos = [c for c in candidatos if c]
    candidatos.sort(key=lambda e: not (host and e.endswith("@" + host)))
    return candidatos[0] if candidatos else ""


def analisar_site(site: str, timeout: float = 10.0, client: httpx.Client | None = None) -> dict:
    """Avalia um site. Nunca levanta exceção para o chamador."""
    r = _base()
    url = _url_completa(site)
    if not url:
        r["sem_site"] = True
        return r
    r["tipo_site"] = "proprio"

    inicio = time.perf_counter()
    resp, erro, cert_ruim = _tentar(url, timeout, client)
    # Maps às vezes aponta para uma subpágina que já não existe: tenta a raiz.
    if resp is not None and resp.status_code == 404 and urlparse(url).path not in ("", "/"):
        resp2, erro2, cert2 = _tentar(_raiz(url), timeout, client)
        if resp2 is not None:
            resp, erro, cert_ruim = resp2, erro2, cert2
    r["tempo_ms"] = int((time.perf_counter() - inicio) * 1000)
    r["certificado_invalido"] = cert_ruim

    if resp is None:
        r["site_quebrado"] = True
        r["erro"] = erro
        return r

    r["http_status"] = resp.status_code
    r["url_final"] = str(resp.url)
    r["sem_https"] = not str(resp.url).startswith("https://") or cert_ruim

    cloudflare = "cloudflare" in resp.headers.get("server", "").lower()
    if resp.status_code in (401, 403, 429) or (resp.status_code == 503 and cloudflare):
        r["bloqueou_verificacao"] = True
        return r
    if resp.status_code >= 400:
        r["site_quebrado"] = True
        r["erro"] = f"HTTP {resp.status_code}"
        return r

    r["analisou_site"] = True
    html = (resp.text or "")[:600_000]
    r["lento"] = r["tempo_ms"] > LIMITE_LENTO_MS
    r["sem_mobile"] = not VIEWPORT.search(html)

    anos = [int(a) for a in ANO_RODAPE.findall(html)]
    if anos:
        r["ano_rodape"] = max(anos)
    if (r["ano_rodape"] and r["ano_rodape"] <= datetime.now().year - ANOS_DESATUALIZADO) or SINAIS_LEGADO.search(html):
        r["parece_desatualizado"] = True

    if SINAIS_AGENDAMENTO.search(html):
        r["tem_agendamento_online"] = True
        r["sem_agendamento_online"] = False
    r["tem_loja"] = bool(SINAIS_LOJA.search(html))
    r["tem_area_cliente"] = bool(SINAIS_AREA_CLIENTE.search(html))
    r["tem_app"] = bool(SINAIS_APP.search(html))
    if SINAIS_WHATSAPP.search(html):
        r["tem_whatsapp"] = True
        r["sem_whatsapp"] = False
    for nome, rx in PLATAFORMAS:
        if rx.search(html):
            r["plataforma"] = nome
            break
    r["redes_encontradas"] = _redes(html)
    r["email_encontrado"] = _email(html, str(resp.url))

    maduro = sum(
        [
            r["tem_agendamento_online"],
            r["tem_area_cliente"],
            r["tem_loja"],
            r["tem_app"],
            not r["sem_https"],
            not r["sem_mobile"],
            not r["parece_desatualizado"],
        ]
    )
    if maduro >= 5 and (r["tem_agendamento_online"] or r["tem_area_cliente"] or r["tem_app"]):
        r["sistema_proprio_maduro"] = True
    return r


def enriquecer_lead(lead: dict, client: httpx.Client | None = None) -> dict:
    """Enriquece um lead. Links de rede social, WhatsApp e agregadores não contam como site."""
    site = (lead.get("site") or "").strip()
    tipo = lead.get("tipo_site") or classificar_site(site)
    tem_social = bool((lead.get("instagram") or "").strip() or (lead.get("facebook") or "").strip())

    if tipo == "proprio":
        r = analisar_site(site, client=client)
    else:
        r = _base()
        r["sem_site"] = True
        r["tipo_site"] = tipo
        r["so_rede_social"] = tipo in ("rede_social", "agregador") or tem_social
        r["orgao_publico"] = tipo == "orgao_publico"
        if any(d in site.lower() for d in DOMINIOS_AGENDAMENTO):
            r["tem_agendamento_online"] = True
            r["sem_agendamento_online"] = False
        if tipo == "whatsapp":
            r["tem_whatsapp"] = True
            r["sem_whatsapp"] = False

    r["whatsapp_provavel"] = r["tem_whatsapp"] or eh_celular(lead.get("telefone"))
    sinais = lead.get("sinais_maps") or {}
    if sinais.get("reserva_pelo_google"):
        r["tem_agendamento_online"] = True
        r["sem_agendamento_online"] = False
        r["reserva_pelo_google"] = True
    if sinais.get("pedido_online_google"):
        r["pedido_online_google"] = True
    r["perfil_reivindicado"] = bool(sinais.get("perfil_reivindicado"))
    return r


def _falha(lead: dict, e: Exception) -> dict:
    r = _base()
    r["sem_site"] = not bool(lead.get("site"))
    r["erro"] = f"falha no enriquecimento: {e}"[:200]
    return r


def enriquecer_lote(leads: list[dict], workers: int = 8, progresso=None) -> list[dict]:
    """Processa em paralelo. Erro em um item não aborta o lote."""
    saida: list[dict] = [{}] * len(leads)

    def trabalho(i: int):
        try:
            return i, enriquecer_lead(leads[i])
        except Exception as e:  # noqa: BLE001 - nunca derruba a campanha
            return i, _falha(leads[i], e)

    feitos = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, enriq in ex.map(trabalho, range(len(leads))):
            saida[i] = enriq
            feitos += 1
            if progresso and (feitos % 10 == 0 or feitos == len(leads)):
                progresso(f"Enriquecimento: {feitos}/{len(leads)} sites")
    return saida
