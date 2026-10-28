"""Pipeline: coleta → normalização → enriquecimento → pontuação → CSV/DB."""
from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.coleta import CampanhaCancelada, baixar_csv, executar_coleta
from app.config_loader import pesos as carregar_pesos
from app.config_loader import segmento_por_id, segmento_por_termo
from app.enriquecimento import enriquecer_lote
from app.normalizacao import deduplicar, esta_fechado, linha_kit_para_lead
from app.pontuacao import pontuar_detalhado

ROOT = Path(__file__).resolve().parent.parent
EXPORTS = ROOT / "data" / "exports"
BRUTOS = ROOT / "data" / "brutos"

# Status que o pipeline pode reavaliar sozinho. Os demais são decisão da equipe.
STATUS_AUTOMATICOS = {"novo", "qualificado"}

CAMPOS_CSV = [
    "pontuacao",
    "motivos",
    "nome",
    "telefone",
    "email",
    "site",
    "categoria",
    "endereco",
    "bairro",
    "nota",
    "avaliacoes",
    "instagram",
    "facebook",
    "maps_url",
    "segmento_id",
    "status",
    "proximo_passo",
    "coletado_em",
]

# Colunas gravadas a cada coleta (dados do Maps + enriquecimento + pontuação).
CAMPOS_COLETA = [
    "nome", "telefone", "email", "site", "tipo_site", "categoria", "endereco", "bairro",
    "nota", "avaliacoes", "instagram", "facebook", "linkedin", "latitude", "longitude",
    "maps_url", "segmento_id", "campanha_id", "pontuacao", "motivos", "enriquecimento",
    "componentes",
]


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status_automatico(score: int, atual: str | None) -> str:
    limiar = int(carregar_pesos().get("limiar_qualificado", 55))
    if atual and atual not in STATUS_AUTOMATICOS:
        return atual
    return "qualificado" if score >= limiar else "novo"


def _completar_com_enriquecimento(lead: dict, enriq: dict) -> None:
    """Redes e e-mail achados no site entram no lead se o Maps não trouxe."""
    for rede, url in (enriq.get("redes_encontradas") or {}).items():
        if not lead.get(rede):
            lead[rede] = url
    if not lead.get("email") and enriq.get("email_encontrado"):
        lead["email"] = enriq["email_encontrado"]


def salvar_bruto(rows: list[dict], destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    campos = list(dict.fromkeys(k for r in rows for k in r))
    with open(destino, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        if campos:
            w.writeheader()
            w.writerows(rows)
    return destino


def rodar_campanha(
    *,
    nome: str,
    termos: list[str],
    cidade: str = "São Luís, MA, Brasil",
    segmento_id: str | None = None,
    profundidade: int | None = None,
    grid: int | None = None,
    log=print,
    cancelar: threading.Event | None = None,
) -> dict:
    db.inicializar()
    if not segmento_id and termos:
        seg = segmento_por_termo(termos[0])
        segmento_id = seg["id"] if seg else None
    cfg_coleta = carregar_pesos().get("coleta", {})

    with db.sessao() as conn:
        cur = conn.execute(
            """INSERT INTO campanhas
               (nome, segmento_id, termos, cidade, profundidade, grid, status, criada_em)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                nome,
                segmento_id,
                json.dumps(termos, ensure_ascii=False),
                cidade,
                profundidade or cfg_coleta.get("profundidade_padrao", 5),
                grid or cfg_coleta.get("grid_padrao", 1),
                "rodando",
                _agora(),
            ),
        )
        campanha_id = cur.lastrowid

    try:
        rows, meta = executar_coleta(
            termos, cidade=cidade, profundidade=profundidade, grid=grid, log=log, cancelar=cancelar
        )
    except Exception as e:
        status = "cancelada" if isinstance(e, CampanhaCancelada) else "erro"
        with db.sessao() as conn:
            conn.execute(
                "UPDATE campanhas SET status=?, erro=?, bloqueio_suspeito=?, finalizada_em=? WHERE id=?",
                (status, str(e)[:500], 0 if status == "cancelada" else 1, _agora(), campanha_id),
            )
        raise

    salvar_bruto(rows, BRUTOS / f"campanha-{campanha_id}.csv")
    return processar_linhas(rows, campanha_id=campanha_id, segmento_id=segmento_id, meta=meta, log=log)


def _linhas_brutas(campanha: dict) -> list[dict]:
    """CSV bruto salvo em data/brutos; se não houver, baixa de novo do motor (sem nova busca)."""
    arquivo = BRUTOS / f"campanha-{campanha['id']}.csv"
    if arquivo.exists() and arquivo.stat().st_size:
        with open(arquivo, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    rows: list[dict] = []
    for job_id in campanha.get("job_ids") or []:
        rows.extend(baixar_csv(job_id))
    if rows:
        salvar_bruto(rows, arquivo)
    return rows


def reprocessar_campanha(campanha_id: int, log=print) -> dict:
    """Refaz normalização, enriquecimento e pontuação de uma campanha sem consultar o Google."""
    db.inicializar()
    with db.sessao() as conn:
        campanha = db.row_para_dict(conn.execute("SELECT * FROM campanhas WHERE id = ?", (campanha_id,)).fetchone())
    if not campanha:
        raise ValueError("Campanha não encontrada.")
    log(f"Reprocessando campanha #{campanha_id} a partir do CSV bruto…")
    rows = _linhas_brutas(campanha)
    if not rows:
        raise RuntimeError("CSV bruto indisponível (nem em data/brutos nem no motor).")
    meta = {
        "job_ids": campanha.get("job_ids") or [],
        "bloqueio_suspeito": bool(campanha.get("bloqueio_suspeito")),
        "fora_da_cidade": campanha.get("fora_da_cidade") or 0,
    }
    return processar_linhas(
        rows, campanha_id=campanha_id, segmento_id=campanha.get("segmento_id"), meta=meta, log=log
    )


def processar_linhas(rows: list[dict], *, campanha_id: int, segmento_id: str | None, meta: dict, log=print) -> dict:
    cfg_coleta = carregar_pesos().get("coleta", {})
    rows = deduplicar(rows)
    fechados = 0
    if cfg_coleta.get("descartar_fechados", True):
        antes = len(rows)
        rows = [r for r in rows if not esta_fechado(r)]
        fechados = antes - len(rows)
    log(f"Após dedupe: {len(rows)} empresas" + (f" ({fechados} fechadas descartadas)" if fechados else ""))

    leads = [linha_kit_para_lead(r, segmento_id, campanha_id) for r in rows]
    leads = [L for L in leads if L.get("nome")]

    log(f"Verificando presença digital de {len(leads)} empresas…")
    enriqs = enriquecer_lote(leads, progresso=log)

    novos = atualizados = 0
    resultados = []
    with db.sessao() as conn:
        for lead, enriq in zip(leads, enriqs):
            _completar_com_enriquecimento(lead, enriq)
            d = pontuar_detalhado(
                avaliacoes=lead.get("avaliacoes") or 0,
                nota=lead.get("nota"),
                segmento_id=segmento_id,
                categoria=lead.get("categoria"),
                enriquecimento=enriq,
            )
            motivos = d["motivos"]
            existente = None
            if lead.get("place_id"):
                existente = conn.execute(
                    "SELECT id, status FROM leads WHERE place_id = ?", (lead["place_id"],)
                ).fetchone()
            status = _status_automatico(d["pontuacao"], existente["status"] if existente else None)
            if db.esta_na_lista_nao_contatar(conn, lead.get("telefone"), lead.get("email")):
                status = "descartado"
                motivos += "; na lista não contatar"

            valores = {
                **{k: lead.get(k) for k in CAMPOS_COLETA},
                "pontuacao": d["pontuacao"],
                "motivos": motivos,
                "enriquecimento": json.dumps(enriq, ensure_ascii=False),
                "componentes": json.dumps(d["componentes"], ensure_ascii=False),
                "segmento_id": segmento_id,
                "campanha_id": campanha_id,
                "status": status,
                "atualizado_em": _agora(),
            }
            for k in ("telefone", "email", "site", "categoria", "endereco", "bairro", "instagram",
                      "facebook", "linkedin", "maps_url", "tipo_site"):
                valores[k] = valores.get(k) or ""

            if existente:
                lead_id = existente["id"]
                sets = ", ".join(f"{k}=?" for k in valores)
                conn.execute(f"UPDATE leads SET {sets} WHERE id=?", (*valores.values(), lead_id))
                if existente["status"] != status:
                    db.registrar_historico(conn, lead_id, "status", existente["status"], status, "coleta")
                atualizados += 1
            else:
                valores.update(place_id=lead.get("place_id"), origem="google_maps", coletado_em=valores["atualizado_em"])
                cols = ", ".join(valores)
                marcas = ", ".join("?" for _ in valores)
                lead_id = conn.execute(f"INSERT INTO leads ({cols}) VALUES ({marcas})", tuple(valores.values())).lastrowid
                novos += 1
            resultados.append({**valores, "id": lead_id})

        conn.execute(
            """UPDATE campanhas SET status=?, job_ids=?, bloqueio_suspeito=?, linhas_coletadas=?,
               fora_da_cidade=?, fechados=?, finalizada_em=? WHERE id=?""",
            (
                "ok",
                json.dumps(meta.get("job_ids", [])),
                1 if meta.get("bloqueio_suspeito") else 0,
                len(resultados),
                meta.get("fora_da_cidade", 0),
                fechados,
                _agora(),
                campanha_id,
            ),
        )

    resultados.sort(key=lambda x: (-x["pontuacao"], x["nome"]))
    csv_path = exportar_csv(resultados, f"campanha-{campanha_id}-ordenado.csv")
    log(f"{novos} novos, {atualizados} já existiam (atualizados). CSV: {csv_path.name}")
    seg_nome = (segmento_por_id(segmento_id) or {}).get("nome", segmento_id)
    return {
        "campanha_id": campanha_id,
        "segmento": seg_nome,
        "total": len(resultados),
        "novos": novos,
        "atualizados": atualizados,
        "csv": str(csv_path),
        "meta": meta,
        "leads": resultados,
    }


def repontuar_todos() -> dict:
    """Recalcula pontuação com os pesos atuais, sem nova coleta nem nova visita aos sites."""
    db.inicializar()
    mudaram = 0
    with db.sessao() as conn:
        rows = conn.execute("SELECT * FROM leads").fetchall()
        for row in rows:
            lead = db.row_para_dict(row)
            enriq = lead.get("enriquecimento") if isinstance(lead.get("enriquecimento"), dict) else {}
            d = pontuar_detalhado(
                avaliacoes=lead.get("avaliacoes") or 0,
                nota=lead.get("nota"),
                segmento_id=lead.get("segmento_id"),
                categoria=lead.get("categoria"),
                enriquecimento=enriq,
            )
            motivos = d["motivos"]
            status = lead["status"]
            if db.esta_na_lista_nao_contatar(conn, lead.get("telefone"), lead.get("email")):
                motivos += "; na lista não contatar"
            else:
                status = _status_automatico(d["pontuacao"], lead["status"])
            if d["pontuacao"] != lead["pontuacao"] or status != lead["status"] or motivos != lead["motivos"]:
                mudaram += 1
            conn.execute(
                "UPDATE leads SET pontuacao=?, motivos=?, componentes=?, status=? WHERE id=?",
                (d["pontuacao"], motivos, json.dumps(d["componentes"], ensure_ascii=False), status, lead["id"]),
            )
            if status != lead["status"]:
                db.registrar_historico(conn, lead["id"], "status", lead["status"], status, "repontuação")
    return {"total": len(rows), "alterados": mudaram}


def exportar_csv(leads: list[dict], nome_arquivo: str) -> Path:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    path = EXPORTS / nome_arquivo
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_CSV, extrasaction="ignore")
        w.writeheader()
        for row in leads:
            w.writerow(row)
    return path


def exportar_filtrado(filtros: dict) -> Path:
    """Exporta a visão filtrada do banco, na mesma ordem do painel."""
    db.inicializar()
    where, params = db.filtros_leads(filtros)
    ordem = db.ORDENACOES.get(filtros.get("ordem") or "", db.ORDENACOES["pontuacao"])
    with db.sessao() as conn:
        rows = [db.row_para_dict(r) for r in conn.execute(f"SELECT * FROM leads{where} ORDER BY {ordem}", params)]
    nome = f"export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return exportar_csv(rows, nome)
