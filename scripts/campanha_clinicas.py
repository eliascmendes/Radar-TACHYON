#!/usr/bin/env python3
"""Exemplo de campanha pela linha de comando: clínicas odontológicas em São Luís → CSV ordenado com motivos."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.coleta import geocodificar, motor_no_ar
from app.pipeline import rodar_campanha


def main() -> int:
    print("=== Radar TACHYON · campanha de exemplo ===")
    lat, lon, fallback = geocodificar("São Luís, MA, Brasil")
    print(f"Geocode Sao Luis, MA, Brasil -> {lat}, {lon} (fallback={fallback})")
    if not motor_no_ar():
        print("ERRO: scraper offline em http://127.0.0.1:8080")
        print("Suba com: .\\subir.ps1   ou   cd google-maps-scraper-kit; docker compose up -d")
        return 1

    res = rodar_campanha(
        nome="Clínica odontológica · São Luís",
        termos=["clínica odontológica"],
        cidade="São Luís, MA, Brasil",
        segmento_id="clinicas",
        profundidade=5,
        log=print,
    )
    print(f"\nTotal: {res['total']} leads")
    print(f"CSV: {res['csv']}")
    print("Top 5:")
    for L in res["leads"][:5]:
        print(f"  [{L['pontuacao']}] {L['nome']} · {L['motivos']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
