"""Coleta via API do google-maps-scraper-kit (Docker em 127.0.0.1:8080).

Segue o mesmo protocolo do scripts/scrape.py do kit: um job por vez, polling a
cada 8 s, download do CSV. Com grid > 1 a cidade é dividida em N x N células
(bbox do Nominatim) e o resultado é filtrado para dentro da cidade.
"""
from __future__ import annotations

import csv
import io
import math
import os
import threading
import time
from typing import Callable

import httpx

from app.config_loader import pesos as carregar_pesos

KIT_BASE = os.environ.get("RADAR_KIT_URL", "http://127.0.0.1:8080").rstrip("/")
UA = "RadarTachyon/1.1 (TACHYON Sao Luis; coleta responsavel)"
FALLBACK_LAT = -2.5307
FALLBACK_LON = -44.3068
# Ilha de São Luís (aprox.): usado se o Nominatim não devolver bbox.
FALLBACK_BBOX = ["-2.80", "-2.40", "-44.45", "-44.00"]
CIDADE_PADRAO = "São Luís, MA, Brasil"

csv.field_size_limit(50_000_000)  # colunas de reviews do motor são enormes

_geo_cache: dict[str, dict] = {}
_geo_lock = threading.Lock()


class CampanhaCancelada(RuntimeError):
    pass


def motor_no_ar(timeout: float = 3.0) -> bool:
    try:
        r = httpx.get(f"{KIT_BASE}/api/v1/jobs", timeout=timeout, headers={"User-Agent": UA})
        return r.status_code == 200
    except Exception:
        return False


def _parece_sao_luis(q: str, lat: float, lon: float) -> bool:
    if "são luís" not in q.lower() and "sao luis" not in q.lower():
        return True
    return abs(lat - FALLBACK_LAT) <= 1.5 and abs(lon - FALLBACK_LON) <= 1.5


def geocodificar_completo(cidade: str) -> dict:
    """{lat, lon, bbox, usou_fallback}. Cacheado: o Nominatim pede ~1 req/s."""
    cfg = carregar_pesos()
    q = (cidade or cfg.get("cidade_padrao", CIDADE_PADRAO)).strip()
    with _geo_lock:
        if q in _geo_cache:
            return _geo_cache[q]
        fallback = {
            "lat": str(cfg.get("fallback_lat", FALLBACK_LAT)),
            "lon": str(cfg.get("fallback_lon", FALLBACK_LON)),
            "bbox": FALLBACK_BBOX,
            "usou_fallback": True,
        }
        try:
            r = httpx.get(
                "https://nominatim.openstreetmap.org/search",
                params={"format": "json", "limit": 1, "q": q},
                headers={"User-Agent": UA},
                timeout=20,
            )
            time.sleep(1)
            hits = r.json()
            if hits:
                lat, lon = float(hits[0]["lat"]), float(hits[0]["lon"])
                if _parece_sao_luis(q, lat, lon):
                    res = {
                        "lat": str(hits[0]["lat"]),
                        "lon": str(hits[0]["lon"]),
                        "bbox": hits[0].get("boundingbox") or FALLBACK_BBOX,
                        "usou_fallback": False,
                    }
                    _geo_cache[q] = res
                    return res
        except Exception:
            # rede/rate-limit: usa o fallback, mas não cacheia para tentar de novo depois
            return fallback
        _geo_cache[q] = fallback
        return fallback


def geocodificar(cidade: str) -> tuple[str, str, bool]:
    """Compatível com a versão anterior: (lat, lon, usou_fallback)."""
    g = geocodificar_completo(cidade)
    return g["lat"], g["lon"], g["usou_fallback"]


def celulas_grid(bbox: list, n: int) -> list[dict]:
    """Divide bbox [sul, norte, oeste, leste] em n x n células (mesma conta do kit)."""
    s, nn, w, e = (float(x) for x in bbox)
    dlat, dlon = (nn - s) / n, (e - w) / n
    m_por_grau = 6371000 * math.pi / 180
    cells = []
    for i in range(n):
        for j in range(n):
            lat, lon = s + (i + 0.5) * dlat, w + (j + 0.5) * dlon
            cos = math.cos(math.radians(lat))
            largura = max(1.0, dlon * m_por_grau * cos)
            diag = math.hypot(dlat * m_por_grau, largura)
            zoom = round(math.log2(156543.03 * cos * 1000 / largura))
            cells.append(
                {
                    "lat": f"{lat:.6f}",
                    "lon": f"{lon:.6f}",
                    "radius": max(1000, math.ceil(1.15 * diag / 2)),
                    "zoom": min(17, max(11, zoom)),
                }
            )
    return cells


def dentro_bbox(row: dict, bbox: list, margem: float = 0.10) -> bool:
    try:
        lat, lon = float(row.get("latitude")), float(row.get("longitude"))
    except (TypeError, ValueError):
        return True
    s, n, w, e = (float(x) for x in bbox)
    my, mx = (n - s) * margem, (e - w) * margem
    return s - my <= lat <= n + my and w - mx <= lon <= e + mx


def _criar_job(body: dict) -> str:
    r = httpx.post(f"{KIT_BASE}/api/v1/jobs", json=body, headers={"User-Agent": UA}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Falha ao criar job HTTP {r.status_code}: {r.text[:300]}")
    job_id = r.json().get("id")
    if not job_id:
        raise RuntimeError("API não retornou id do job.")
    return job_id


def _aguardar_job(
    job_id: str,
    poll_s: int = 8,
    max_tentativas: int = 150,
    log: Callable | None = None,
    cancelar: threading.Event | None = None,
) -> None:
    ultimo = None
    for i in range(max_tentativas):
        if cancelar is not None and cancelar.is_set():
            raise CampanhaCancelada("Campanha cancelada pelo usuário.")
        try:
            r = httpx.get(f"{KIT_BASE}/api/v1/jobs/{job_id}", headers={"User-Agent": UA}, timeout=30)
            status = r.json().get("Status")
        except Exception as e:  # motor reiniciando: tenta de novo
            status = f"erro ({e.__class__.__name__})"
        if log and status != ultimo:
            log(f"Job {job_id[:8]}: {status}")
            ultimo = status
        if status == "ok":
            return
        if status == "failed":
            raise RuntimeError("Job falhou. Possível bloqueio temporário do Google. Aguarde ou reduza o volume.")
        if cancelar is not None:
            cancelar.wait(poll_s)
        else:
            time.sleep(poll_s)
    raise RuntimeError("Tempo esgotado aguardando o job do scraper.")


def baixar_csv(job_id: str) -> list[dict]:
    r = httpx.get(f"{KIT_BASE}/api/v1/jobs/{job_id}/download", headers={"User-Agent": UA}, timeout=180)
    r.raise_for_status()
    texto = r.content.decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(texto)))


def _rodar_job(body: dict, log: Callable | None, cancelar: threading.Event | None) -> tuple[str, list[dict]]:
    job_id = _criar_job(body)
    _aguardar_job(job_id, log=log, cancelar=cancelar)
    return job_id, baixar_csv(job_id)


def executar_coleta(
    termos: list[str],
    cidade: str = CIDADE_PADRAO,
    profundidade: int | None = None,
    grid: int | None = None,
    max_time: int | None = None,
    log: Callable | None = None,
    cancelar: threading.Event | None = None,
) -> tuple[list[dict], dict]:
    """Um job por vez. Retorna (linhas brutas, meta)."""
    cfg = carregar_pesos().get("coleta", {})
    profundidade = profundidade or int(cfg.get("profundidade_padrao", 5))
    grid = max(1, min(5, int(grid or cfg.get("grid_padrao", 1))))
    max_time = max_time or int(cfg.get("max_time_segundos", 600))
    idioma = cfg.get("idioma", "pt")
    log = log or (lambda _m: None)

    if not motor_no_ar():
        raise RuntimeError(
            f"Motor do scraper não responde em {KIT_BASE}. "
            "Suba com: cd google-maps-scraper-kit && docker compose up -d"
        )

    keywords = [t.strip() for t in termos if t and t.strip()]
    if not keywords:
        raise ValueError("Nenhum termo de busca informado.")
    nome_cidade = cidade.split(",")[0].strip()
    kws = [k if " em " in k.lower() else f"{k} em {nome_cidade}" for k in keywords]

    log(f'Geocodificando "{cidade}"…')
    geo = geocodificar_completo(cidade)
    log(f"Coordenadas ({'fallback fixo' if geo['usou_fallback'] else 'Nominatim'}): {geo['lat']}, {geo['lon']}")

    base = {
        "name": "radar-tachyon",
        "keywords": kws,
        "lang": idioma,
        "fast_mode": False,
        "depth": profundidade,
        "email": True,
        "max_time": max_time,
    }
    if grid == 1:
        alvos = [{"lat": geo["lat"], "lon": geo["lon"], "zoom": 14, "radius": 12000}]
    else:
        alvos = celulas_grid(geo["bbox"], grid)
    log(f"{len(kws)} termo(s), profundidade {profundidade}, {len(alvos)} job(s) em sequência")

    rows: list[dict] = []
    job_ids: list[str] = []
    falhas = 0
    for i, alvo in enumerate(alvos, 1):
        if len(alvos) > 1:
            log(f"Célula {i}/{len(alvos)} @ {alvo['lat']},{alvo['lon']} raio {alvo['radius']} m")
        try:
            job_id, linhas = _rodar_job({**base, **alvo}, log, cancelar)
        except CampanhaCancelada:
            raise
        except RuntimeError as e:
            if len(alvos) == 1:
                raise
            falhas += 1
            log(f"Célula {i} ignorada: {e}")
            continue
        job_ids.append(job_id)
        rows.extend(linhas)
        log(f"{len(linhas)} linhas brutas (acumulado {len(rows)})")

    if not job_ids:
        raise RuntimeError("Todas as células falharam. Possível bloqueio: aguarde antes de tentar de novo.")

    fora = 0
    if grid > 1 and cfg.get("descartar_fora_da_cidade", True):
        antes = len(rows)
        rows = [r for r in rows if dentro_bbox(r, geo["bbox"])]
        fora = antes - len(rows)
        if fora:
            log(f"{fora} linhas fora da cidade descartadas")

    bloqueio = len(rows) == 0 or falhas > 0
    if len(rows) == 0:
        log("ALERTA: zero resultados. Possível bloqueio ou termo sem cobertura.")
    meta = {
        "lat": geo["lat"],
        "lon": geo["lon"],
        "usou_fallback_geo": geo["usou_fallback"],
        "job_ids": job_ids,
        "grid": grid,
        "celulas_falharam": falhas,
        "fora_da_cidade": fora,
        "bloqueio_suspeito": bloqueio,
        "linhas": len(rows),
    }
    return rows, meta
