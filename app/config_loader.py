"""Carrega JSON editáveis (pesos, segmentos, mensagens, equipe)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def _load(nome: str) -> dict:
    caminho = CONFIG / nome
    with open(caminho, encoding="utf-8") as f:
        return json.load(f) or {}


@lru_cache(maxsize=1)
def pesos() -> dict:
    return _load("pesos.json")


@lru_cache(maxsize=1)
def segmentos() -> dict:
    return _load("segmentos.json")


@lru_cache(maxsize=1)
def mensagens() -> dict:
    return _load("mensagens.json")


def recarregar() -> None:
    pesos.cache_clear()
    segmentos.cache_clear()
    mensagens.cache_clear()
    equipe.cache_clear()


def segmento_por_termo(termo: str) -> dict | None:
    termo_l = (termo or "").strip().lower()
    for seg in segmentos().get("segmentos", []):
        for t in seg.get("termos", []):
            if t.strip().lower() == termo_l:
                return seg
            if termo_l in t.lower() or t.lower() in termo_l:
                return seg
    return None


def segmento_por_id(seg_id: str) -> dict | None:
    for seg in segmentos().get("segmentos", []):
        if seg.get("id") == seg_id:
            return seg
    return None


@lru_cache(maxsize=1)
def equipe() -> dict:
    """config/equipe.json é local (fora do git); sem ele, vale o arquivo de exemplo."""
    try:
        return _load("equipe.json")
    except FileNotFoundError:
        return _load("equipe.exemplo.json")


def responsaveis() -> list[str]:
    return list(equipe().get("responsaveis") or [])
