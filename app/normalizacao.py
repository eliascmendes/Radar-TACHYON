"""Normalização BR: telefone +55, dedupe, bairro, links, sinais do Maps."""
from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import urlparse

# Domínios que aparecem no campo "website" do Maps mas não são site próprio.
DOMINIOS_REDE_SOCIAL = (
    "instagram.com",
    "facebook.com",
    "fb.com",
    "tiktok.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
)
DOMINIOS_WHATSAPP = ("wa.me", "api.whatsapp.com", "whatsapp.com", "wa.link")
DOMINIOS_AGREGADOR = (
    "linktr.ee",
    "linktree",
    "contate.me",
    "beacons.ai",
    "bio.link",
    "linkbio",
    "taplink",
    "sites.google.com",
    "business.site",
    "negocio.site",
    "doctoralia.com",
    "ifood.com",
    "goomer.app",
    "anota.ai",
)
# Plataformas de agendamento usadas como "site": já resolvem o agendamento.
DOMINIOS_AGENDAMENTO = ("doctoralia.com", "calendly.com", "trinks.com", "booksy.com", "agendasalao", "avec.app")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_EMAIL_LIXO = ("example", "sentry", "wixpress", "domain.com", "email.com", "seudominio", "yourdomain")


def _so_digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def normalizar_telefone(telefone: str | None) -> str:
    """Formato brasileiro: +55 DD XXXXX-XXXX (celular) ou +55 DD XXXX-XXXX (fixo)."""
    digitos = _so_digitos(telefone)
    if not digitos:
        return ""
    if digitos.startswith("55") and len(digitos) >= 12:
        digitos = digitos[2:]
    if digitos.startswith("0") and len(digitos) in (11, 12):
        digitos = digitos[1:]
    if len(digitos) == 10:
        ddd, num = digitos[:2], digitos[2:]
        return f"+55 {ddd} {num[:4]}-{num[4:]}"
    if len(digitos) == 11:
        ddd, num = digitos[:2], digitos[2:]
        return f"+55 {ddd} {num[:5]}-{num[5:]}"
    if len(digitos) >= 8:
        return f"+55 {digitos}"
    return digitos


def digitos_telefone(telefone: str | None) -> str:
    """Chave comparável (DDD + número, sem 55) para dedupe e lista não contatar."""
    d = _so_digitos(telefone)
    if d.startswith("55") and len(d) >= 12:
        d = d[2:]
    return d


def eh_celular(telefone: str | None) -> bool:
    """Celular BR: DDD + 9 dígitos começando com 9. Indica WhatsApp provável."""
    d = digitos_telefone(telefone)
    return len(d) == 11 and d[2] == "9"


def link_whatsapp(telefone: str | None) -> str:
    if not eh_celular(telefone):
        return ""
    return f"https://wa.me/55{digitos_telefone(telefone)}"


def limpar_categoria(categoria: str | None) -> str:
    if not categoria:
        return ""
    return re.sub(r"\s+", " ", categoria.strip())[:120]


def limpar_email(bruto: str | None) -> str:
    for e in re.split(r"[,;\s]+", bruto or ""):
        e = e.strip().lower()
        if not e or not EMAIL_RE.match(e):
            continue
        if any(lixo in e for lixo in _EMAIL_LIXO) or e.endswith((".png", ".jpg", ".webp", ".svg")):
            continue
        return e
    return ""


def classificar_site(site: str | None) -> str:
    """'' (sem site), 'proprio', 'rede_social', 'whatsapp', 'agregador' ou 'orgao_publico'."""
    site = (site or "").strip()
    if not site:
        return ""
    url = site if site.startswith(("http://", "https://")) else "https://" + site
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    alvo = host + (urlparse(url).path or "")
    if host.endswith(".gov.br") or host == "gov.br":
        return "orgao_publico"
    if any(host == d or host.endswith("." + d) for d in DOMINIOS_WHATSAPP):
        return "whatsapp"
    if any(host == d or host.endswith("." + d) for d in DOMINIOS_REDE_SOCIAL):
        return "rede_social"
    if any(d in alvo for d in DOMINIOS_AGREGADOR + DOMINIOS_AGENDAMENTO):
        return "agregador"
    return "proprio"


def _json(texto: str | None):
    if not texto or texto in ("null", "[]", "{}"):
        return None
    try:
        return json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return None


_BAIRRO_RE = re.compile(r"-\s*([^,\-]+?),\s*[^,\-]+?\s*-\s*[A-Z]{2}\b")


def extrair_bairro(endereco: str | None, completo: str | None = None) -> str:
    """Prefere complete_address.borough do kit; senão lê 'Rua, nº - Bairro, Cidade - UF'."""
    dados = _json(completo)
    if isinstance(dados, dict) and (dados.get("borough") or "").strip():
        return dados["borough"].strip()[:80]
    if not endereco:
        return ""
    achados = _BAIRRO_RE.findall(endereco)
    if achados:
        candidato = achados[-1].strip()
        if not re.search(r"\d{5}", candidato):
            return candidato[:80]
    return ""


def sinais_maps(row: dict) -> dict:
    """Sinais extras do Google Maps que ajudam a pontuação."""
    reservas = _json(row.get("reservations"))
    pedidos = _json(row.get("order_online"))
    dono = _json(row.get("owner"))
    horario = _json(row.get("open_hours"))
    return {
        "reserva_pelo_google": bool(reservas),
        "pedido_online_google": bool(pedidos),
        "perfil_reivindicado": bool(isinstance(dono, dict) and dono.get("id")),
        "tem_horario": bool(horario),
    }


def esta_fechado(row: dict) -> bool:
    status = (row.get("status") or "").lower()
    return "fechad" in status or "closed" in status


def chave_dedupe(row: dict) -> str:
    for k in ("place_id", "cid", "link"):
        v = (row.get(k) or "").strip()
        if v:
            return f"{k}:{v}"
    nome = _normalizar_chave(row.get("title") or row.get("nome") or "")
    end = _normalizar_chave(row.get("address") or row.get("endereco") or "")
    return f"ta:{nome}|{end}"


def _normalizar_chave(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower().strip())


def deduplicar(rows: list[dict]) -> list[dict]:
    vistos: set[str] = set()
    saida: list[dict] = []
    for r in rows:
        k = chave_dedupe(r)
        if k in vistos:
            continue
        vistos.add(k)
        saida.append(r)
    return saida


def _float(v) -> float | None:
    try:
        f = float(v)
        return f if f else None
    except (TypeError, ValueError):
        return None


def linha_kit_para_lead(row: dict, segmento_id: str | None, campanha_id: int | None) -> dict:
    """Converte linha do scraper kit para o modelo interno."""
    nome = (row.get("title") or row.get("nome") or "").strip()
    endereco = (row.get("address") or row.get("endereco") or "").strip()
    # o motor prefixa o endereço com o nome do lugar ("Nome - Rua, nº ...")
    if nome and endereco.startswith(nome + " - "):
        endereco = endereco[len(nome) + 3 :]
    site = (row.get("website") or row.get("site") or "").strip()
    try:
        avaliacoes = int(float(row.get("review_count") or row.get("avaliacoes") or 0))
    except (TypeError, ValueError):
        avaliacoes = 0
    telefone = normalizar_telefone(row.get("phone") or row.get("telefone"))
    tipo_site = classificar_site(site)
    instagram = (row.get("instagram") or "").strip()
    facebook = (row.get("facebook") or "").strip()
    if tipo_site == "rede_social":
        if "instagram.com" in site and not instagram:
            instagram = site
        if "facebook.com" in site and not facebook:
            facebook = site
    return {
        "place_id": (row.get("place_id") or "").strip() or None,
        "nome": nome,
        "telefone": telefone,
        "email": limpar_email(row.get("emails") or row.get("email")),
        "site": site,
        "tipo_site": tipo_site,
        "categoria": limpar_categoria(row.get("category") or row.get("categoria")),
        "endereco": endereco,
        "bairro": extrair_bairro(endereco, row.get("complete_address")),
        "nota": _float(row.get("review_rating") or row.get("nota")),
        "avaliacoes": avaliacoes,
        "instagram": instagram,
        "facebook": facebook,
        "linkedin": (row.get("linkedin") or "").strip(),
        "latitude": _float(row.get("latitude")),
        "longitude": _float(row.get("longitude")),
        "maps_url": (row.get("link") or "").strip(),
        "sinais_maps": sinais_maps(row),
        "segmento_id": segmento_id,
        "campanha_id": campanha_id,
        "origem": "google_maps",
    }
