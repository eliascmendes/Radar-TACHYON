"""API e painel Radar TACHYON."""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import quote
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db
from app.coleta import KIT_BASE, geocodificar_completo, motor_no_ar
from app.config_loader import mensagens, pesos, recarregar, responsaveis, segmentos
from app.normalizacao import digitos_telefone, link_whatsapp
from app.pipeline import exportar_filtrado, reprocessar_campanha, repontuar_todos, rodar_campanha
from app.rascunhos import gerar_rascunho

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.inicializar()
    yield


app = FastAPI(title="Radar TACHYON", version="1.1.0", lifespan=lifespan)
_campanha_lock = threading.Lock()
_cancelar = threading.Event()
_campanha_progresso: dict[str, Any] = {"rodando": False, "log": []}


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Ordem do funil comercial; o rótulo é o que aparece no painel.
STATUS = [
    ("novo", "Novo"),
    ("qualificado", "Qualificado"),
    ("contatado", "Contatado"),
    ("respondeu", "Respondeu"),
    ("reuniao", "Reunião"),
    ("proposta", "Proposta"),
    ("fechado", "Fechado"),
    ("perdido", "Perdido"),
    ("descartado", "Descartado"),
]
STATUS_VALIDOS = {s for s, _ in STATUS}
STATUS_ABORDAGEM = {"contatado", "respondeu", "reuniao", "proposta"}
STATUS_ENGAJADOS = ("respondeu", "reuniao", "proposta", "fechado")
# Status em que o lead não recebe mais abordagem pelo WhatsApp.
STATUS_SEM_ENVIO = {"fechado", "perdido", "descartado"}


# ── Modelos ──────────────────────────────────────────────────────────────────


class CampanhaIn(BaseModel):
    nome: str = Field(..., min_length=1)
    termos: list[str] = Field(..., min_length=1)
    cidade: str = "São Luís, MA, Brasil"
    segmento_id: str | None = None
    profundidade: int | None = Field(5, ge=1, le=20)
    grid: int | None = Field(1, ge=1, le=5)


class LeadPatch(BaseModel):
    status: str | None = None
    anotacoes: str | None = None
    proximo_passo: str | None = None
    quem: str | None = None


class RascunhoIn(BaseModel):
    quem: str | None = None


class EnvioFiltrosIn(BaseModel):
    segmento_id: str | None = None
    campanha_id: int | None = None
    status: str | None = "novo,qualificado"
    bairro: str | None = None
    pontuacao_min: int | None = None
    sem_site: bool = False
    q: str | None = None
    ids: list[int] | None = None
    incluir_contatados: bool = False
    quem: str | None = None


class EnvioIn(BaseModel):
    texto: str = Field(..., min_length=1)
    quem: str | None = None


class NaoContatarIn(BaseModel):
    telefone: str | None = None
    email: str | None = None
    nome: str | None = None
    motivo: str | None = None
    lead_id: int | None = None


# ── Saúde / config ───────────────────────────────────────────────────────────


@app.get("/api/saude")
def saude():
    geo = geocodificar_completo(pesos().get("cidade_padrao", "São Luís, MA, Brasil"))
    return {
        "ok": True,
        "motor_scraper": motor_no_ar(),
        "motor_url": KIT_BASE,
        "geocode_sao_luis": {"lat": geo["lat"], "lon": geo["lon"], "usou_fallback": geo["usou_fallback"]},
        "campanha_rodando": _campanha_progresso.get("rodando", False),
    }


@app.get("/api/meta")
def meta():
    """Tudo que o painel precisa para montar filtros e formulários."""
    cfg = pesos()
    return {
        "status": [{"id": s, "nome": n} for s, n in STATUS],
        "responsaveis": responsaveis(),
        "segmentos": [
            {"id": s["id"], "nome": s["nome"], "prioridade": s.get("prioridade"), "termos": s.get("termos", [])}
            for s in segmentos().get("segmentos", [])
        ],
        "limiar_qualificado": cfg.get("limiar_qualificado", 55),
        "cidade_padrao": cfg.get("cidade_padrao", "São Luís, MA, Brasil"),
        "coleta": cfg.get("coleta", {}),
    }


@app.get("/api/config/pesos")
def api_pesos():
    return pesos()


@app.get("/api/config/segmentos")
def api_segmentos():
    return segmentos()


@app.get("/api/config/mensagens")
def api_mensagens():
    return mensagens()


@app.post("/api/config/recarregar")
def api_recarregar():
    recarregar()
    return {"ok": True}


@app.get("/api/responsaveis")
def api_responsaveis():
    return responsaveis()


# ── Campanhas ────────────────────────────────────────────────────────────────


@app.get("/api/campanhas")
def listar_campanhas():
    with db.sessao() as conn:
        rows = conn.execute("SELECT * FROM campanhas ORDER BY id DESC LIMIT 100").fetchall()
        return [db.row_para_dict(r) for r in rows]


@app.get("/api/campanhas/progresso")
def progresso_campanha():
    return _campanha_progresso


@app.post("/api/campanhas/cancelar")
def cancelar_campanha():
    if not _campanha_progresso.get("rodando"):
        raise HTTPException(409, "Nenhuma campanha em execução.")
    _cancelar.set()
    return {"ok": True, "msg": "Cancelamento solicitado. O job atual do motor pode terminar sozinho."}


def _iniciar_em_segundo_plano(nome: str, tarefa) -> None:
    """Roda coleta/reprocessamento numa thread, publicando o log em /api/campanhas/progresso."""
    _cancelar.clear()
    _campanha_progresso.update(
        {"rodando": True, "log": [], "resultado": None, "erro": None, "nome": nome, "inicio": _agora()}
    )

    def log(msg: str):
        _campanha_progresso["log"].append({"em": _agora(), "msg": msg})
        del _campanha_progresso["log"][:-300]

    def worker():
        with _campanha_lock:
            try:
                log(f"Iniciando: {nome}")
                res = tarefa(log)
                _campanha_progresso["resultado"] = {
                    k: res.get(k) for k in ("campanha_id", "total", "novos", "atualizados", "csv")
                }
                log(f"Concluída: {res['total']} leads.")
            except Exception as e:  # noqa: BLE001 - erro vai para o painel
                _campanha_progresso["erro"] = str(e)
                log(f"ERRO: {e}")
            finally:
                _campanha_progresso["rodando"] = False

    threading.Thread(target=worker, daemon=True).start()


def _ocupado() -> bool:
    return bool(_campanha_progresso.get("rodando")) or _campanha_lock.locked()


@app.post("/api/campanhas")
def criar_campanha(body: CampanhaIn):
    termos = [t.strip() for t in body.termos if t.strip()]
    if not termos:
        raise HTTPException(400, "Informe ao menos um termo de busca.")
    if _ocupado():
        raise HTTPException(409, "Já existe uma campanha em execução. Um job por vez.")
    if not motor_no_ar():
        raise HTTPException(503, f"Motor do scraper offline em {KIT_BASE}. Suba o Docker do kit.")

    _iniciar_em_segundo_plano(
        body.nome,
        lambda log: rodar_campanha(
            nome=body.nome,
            termos=termos,
            cidade=body.cidade,
            segmento_id=body.segmento_id,
            profundidade=body.profundidade,
            grid=body.grid,
            log=log,
            cancelar=_cancelar,
        ),
    )
    return {"ok": True, "msg": "Campanha iniciada em segundo plano."}


@app.post("/api/campanhas/{campanha_id}/reprocessar")
def reprocessar(campanha_id: int):
    """Refaz a análise dos sites e a pontuação sem consultar o Google de novo."""
    if _ocupado():
        raise HTTPException(409, "Já existe uma campanha em execução.")
    with db.sessao() as conn:
        if not conn.execute("SELECT 1 FROM campanhas WHERE id = ?", (campanha_id,)).fetchone():
            raise HTTPException(404, "Campanha não encontrada")
    _iniciar_em_segundo_plano(
        f"Reprocessar campanha #{campanha_id}", lambda log: reprocessar_campanha(campanha_id, log=log)
    )
    return {"ok": True}


# ── Leads ────────────────────────────────────────────────────────────────────


def _filtros(**kw) -> dict:
    return {k: v for k, v in kw.items() if v not in (None, "")}


@app.get("/api/leads")
def listar_leads(
    segmento_id: str | None = None,
    campanha_id: int | None = None,
    status: str | None = None,
    bairro: str | None = None,
    pontuacao_min: int | None = None,
    pontuacao_max: int | None = None,
    sem_site: bool = False,
    com_telefone: bool = False,
    q: str | None = None,
    ordem: str = "pontuacao",
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    f = _filtros(
        segmento_id=segmento_id, campanha_id=campanha_id, status=status,
        bairro=bairro, pontuacao_min=pontuacao_min, pontuacao_max=pontuacao_max,
        sem_site=sem_site, com_telefone=com_telefone, q=q,
    )
    where, params = db.filtros_leads(f)
    ordem_sql = db.ORDENACOES.get(ordem, db.ORDENACOES["pontuacao"])
    colunas = (
        "id, nome, categoria, bairro, telefone, email, site, tipo_site, instagram, pontuacao, motivos, "
        "status, proximo_passo, nota, avaliacoes, segmento_id, atualizado_em"
    )
    with db.sessao() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT {colunas} FROM leads{where} ORDER BY {ordem_sql} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
        ]
        filtrado = conn.execute(f"SELECT COUNT(*) AS c FROM leads{where}", params).fetchone()["c"]
        total = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]
    for r in rows:
        r["whatsapp"] = link_whatsapp(r.get("telefone"))
    return {"total_db": total, "total_filtrado": filtrado, "itens": rows}


@app.get("/api/resumo")
def resumo():
    limiar = int(pesos().get("limiar_qualificado", 55))
    with db.sessao() as conn:
        r = conn.execute(
            f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN pontuacao >= ? AND status NOT IN ('descartado','perdido') THEN 1 ELSE 0 END) AS acima_limiar,
                   SUM(CASE WHEN status = 'qualificado' THEN 1 ELSE 0 END) AS qualificados,
                   SUM(CASE WHEN status IN ({",".join("?" * len(STATUS_ABORDAGEM))}) THEN 1 ELSE 0 END) AS em_andamento,
                   SUM(CASE WHEN status = 'fechado' THEN 1 ELSE 0 END) AS fechados
                FROM leads""",
            [limiar, *sorted(STATUS_ABORDAGEM)],
        ).fetchone()
    return {k: (r[k] or 0) for k in r.keys()} | {"limiar": limiar}


@app.post("/api/leads/repontuar")
def api_repontuar():
    """Recarrega config e recalcula as pontuações com os pesos atuais."""
    recarregar()
    return repontuar_todos()


@app.get("/api/leads/{lead_id}")
def obter_lead(lead_id: int):
    with db.sessao() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Lead não encontrado")
        hist = [
            db.row_para_dict(h)
            for h in conn.execute("SELECT * FROM historico WHERE lead_id = ? ORDER BY id DESC", (lead_id,))
        ]
        lead = db.row_para_dict(row)
        lead["historico"] = hist
        lead["whatsapp"] = link_whatsapp(lead.get("telefone"))
        lead["nao_contatar"] = db.esta_na_lista_nao_contatar(conn, lead.get("telefone"), lead.get("email"))
        return lead


@app.patch("/api/leads/{lead_id}")
def atualizar_lead(lead_id: int, body: LeadPatch):
    with db.sessao() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Lead não encontrado")
        atual = dict(row)

        if body.status and body.status not in STATUS_VALIDOS:
            raise HTTPException(400, f"Status inválido: {body.status}")
        if body.status in STATUS_ABORDAGEM and db.esta_na_lista_nao_contatar(conn, atual.get("telefone"), atual.get("email")):
            raise HTTPException(403, "Lead está na lista não contatar. Abordagem bloqueada.")

        quem = body.quem if body.quem in responsaveis() else None
        campos = {
            "status": body.status,
            "anotacoes": body.anotacoes,
            "proximo_passo": body.proximo_passo,
        }
        for campo, novo in campos.items():
            if novo is None:
                continue
            anterior = atual.get(campo)
            if str(anterior or "") == str(novo):
                continue
            conn.execute(f"UPDATE leads SET {campo} = ?, atualizado_em = ? WHERE id = ?", (novo, _agora(), lead_id))
            db.registrar_historico(
                conn, lead_id, campo, str(anterior) if anterior is not None else None, str(novo), quem
            )

        return db.row_para_dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())


@app.delete("/api/leads/{lead_id}")
def excluir_lead(lead_id: int):
    """LGPD: exclusão a qualquer momento."""
    with db.sessao() as conn:
        cur = conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Lead não encontrado")
    return {"ok": True}


# ── Rascunhos ────────────────────────────────────────────────────────────────


@app.post("/api/leads/{lead_id}/rascunho")
def api_rascunho(lead_id: int, body: RascunhoIn | None = None):
    with db.sessao() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Lead não encontrado")
        lead = db.row_para_dict(row)
        if db.esta_na_lista_nao_contatar(conn, lead.get("telefone"), lead.get("email")):
            raise HTTPException(403, "Lead na lista não contatar. Rascunho bloqueado.")
        enriq = lead.get("enriquecimento") if isinstance(lead.get("enriquecimento"), dict) else {}
        texto = gerar_rascunho(
            nome=lead.get("nome") or "",
            segmento_id=lead.get("segmento_id"),
            enriquecimento=enriq,
            responsavel=body.quem if body else None,
        )
        conn.execute("UPDATE leads SET rascunho = ?, atualizado_em = ? WHERE id = ?", (texto, _agora(), lead_id))
        db.registrar_historico(conn, lead_id, "rascunho", None, "gerado")
    return {"rascunho": texto, "aviso": "Revise o texto. Nada é enviado sem você confirmar no WhatsApp."}


# ── WhatsApp (envio assistido) ───────────────────────────────────────────────
# O painel abre cada conversa com a mensagem pronta; quem envia é a pessoa.
# Aqui ficam a fila filtrada, o limite diário e o registro de cada envio.


def _limite_diario() -> int:
    return int(pesos().get("envio", {}).get("limite_diario", 30))


def _envios_hoje(conn) -> int:
    inicio_local = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    desde = inicio_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return conn.execute(
        "SELECT COUNT(*) AS c FROM historico WHERE campo = 'whatsapp' AND em >= ?", (desde,)
    ).fetchone()["c"]


def _motivo_sem_envio(conn, lead: dict, ja_enviados: set[int], incluir_contatados: bool) -> str | None:
    if db.esta_na_lista_nao_contatar(conn, lead.get("telefone"), lead.get("email")):
        return "na lista não contatar"
    if lead.get("status") in STATUS_SEM_ENVIO:
        return f"status {dict(STATUS).get(lead['status'], lead['status']).lower()}"
    if not link_whatsapp(lead.get("telefone")):
        return "sem celular para WhatsApp"
    enriq = lead.get("enriquecimento") if isinstance(lead.get("enriquecimento"), dict) else {}
    if enriq.get("orgao_publico"):
        return "órgão público"
    if not incluir_contatados and (lead["id"] in ja_enviados or lead.get("status") in STATUS_ABORDAGEM):
        return "já contatado"
    return None


@app.post("/api/envios/previa")
def previa_envio(body: EnvioFiltrosIn):
    """Monta a fila: quem pode receber (com a mensagem pronta) e quem ficou de fora, com o motivo."""
    f = _filtros(
        segmento_id=body.segmento_id, campanha_id=body.campanha_id, status=body.status, bairro=body.bairro,
        pontuacao_min=body.pontuacao_min, sem_site=body.sem_site, q=body.q,
    )
    where, params = db.filtros_leads(f)
    if body.ids:
        where += f" AND id IN ({','.join('?' * len(body.ids))})"
        params = [*params, *body.ids]
    quem = body.quem if body.quem in responsaveis() else None
    itens, excluidos, telefones = [], [], set()
    with db.sessao() as conn:
        rows = conn.execute(
            f"SELECT * FROM leads{where} ORDER BY {db.ORDENACOES['pontuacao']} LIMIT 500", params
        ).fetchall()
        ja_enviados = {
            r["lead_id"] for r in conn.execute("SELECT DISTINCT lead_id FROM historico WHERE campo = 'whatsapp'")
        }
        for row in rows:
            lead = db.row_para_dict(row)
            motivo = _motivo_sem_envio(conn, lead, ja_enviados, body.incluir_contatados)
            fone = digitos_telefone(lead.get("telefone"))
            if not motivo and fone in telefones:
                motivo = "telefone repetido na fila"
            if motivo:
                excluidos.append({"id": lead["id"], "nome": lead["nome"], "motivo": motivo})
                continue
            telefones.add(fone)
            enriq = lead.get("enriquecimento") if isinstance(lead.get("enriquecimento"), dict) else {}
            itens.append(
                {
                    "id": lead["id"],
                    "nome": lead["nome"],
                    "pontuacao": lead["pontuacao"],
                    "telefone": lead["telefone"],
                    "bairro": lead.get("bairro"),
                    "status": lead["status"],
                    "mensagem": gerar_rascunho(
                        nome=lead["nome"], segmento_id=lead.get("segmento_id"), enriquecimento=enriq, responsavel=quem
                    ),
                }
            )
        enviados_hoje = _envios_hoje(conn)
    resumo: dict[str, int] = {}
    for e in excluidos:
        resumo[e["motivo"]] = resumo.get(e["motivo"], 0) + 1
    return {
        "itens": itens,
        "excluidos": excluidos,
        "resumo_exclusao": resumo,
        "enviados_hoje": enviados_hoje,
        "limite_diario": _limite_diario(),
    }


@app.post("/api/leads/{lead_id}/whatsapp")
def registrar_whatsapp(lead_id: int, body: EnvioIn):
    """Registra o envio e devolve o link do WhatsApp com a mensagem. Novo/qualificado vira contatado."""
    with db.sessao() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Lead não encontrado")
        lead = db.row_para_dict(row)
        if db.esta_na_lista_nao_contatar(conn, lead.get("telefone"), lead.get("email")):
            raise HTTPException(403, "Lead na lista não contatar. Envio bloqueado.")
        if lead["status"] in STATUS_SEM_ENVIO:
            raise HTTPException(409, f"Lead com status {lead['status']}. Envio bloqueado.")
        link = link_whatsapp(lead.get("telefone"))
        if not link:
            raise HTTPException(400, "Lead sem celular para WhatsApp.")
        limite = _limite_diario()
        if _envios_hoje(conn) >= limite:
            raise HTTPException(429, f"Limite de {limite} envios por dia atingido. Continue amanhã.")

        quem = body.quem if body.quem in responsaveis() else None
        texto = body.texto.strip()
        conn.execute("UPDATE leads SET rascunho = ?, atualizado_em = ? WHERE id = ?", (texto, _agora(), lead_id))
        if lead["status"] in ("novo", "qualificado"):
            conn.execute("UPDATE leads SET status = 'contatado', atualizado_em = ? WHERE id = ?", (_agora(), lead_id))
            db.registrar_historico(conn, lead_id, "status", lead["status"], "contatado", quem)
        db.registrar_historico(conn, lead_id, "whatsapp", None, "enviado", quem)
        enviados_hoje = _envios_hoje(conn)
    return {"url": f"{link}?text={quote(texto)}", "enviados_hoje": enviados_hoje, "limite_diario": limite}


# ── Funil ────────────────────────────────────────────────────────────────────


@app.get("/api/funil")
def funil():
    nomes_seg = {s["id"]: s["nome"] for s in segmentos().get("segmentos", [])}
    with db.sessao() as conn:
        contagem = {r["status"]: r["qtd"] for r in conn.execute("SELECT status, COUNT(*) AS qtd FROM leads GROUP BY status")}
        marcas = ",".join("?" * len(STATUS_ENGAJADOS))
        por_segmento = conn.execute(
            f"""SELECT segmento_id,
                      COUNT(*) AS total,
                      SUM(CASE WHEN status = 'qualificado' THEN 1 ELSE 0 END) AS qualificados,
                      SUM(CASE WHEN status = 'contatado' THEN 1 ELSE 0 END) AS contatados,
                      SUM(CASE WHEN status IN ({marcas}) THEN 1 ELSE 0 END) AS engajados,
                      SUM(CASE WHEN status = 'fechado' THEN 1 ELSE 0 END) AS fechados,
                      ROUND(AVG(pontuacao), 0) AS media_pontuacao
               FROM leads GROUP BY segmento_id ORDER BY total DESC""",
            STATUS_ENGAJADOS,
        ).fetchall()
    taxas = []
    for s in por_segmento:
        abordados = (s["contatados"] or 0) + (s["engajados"] or 0)
        taxas.append(
            {
                "segmento_id": s["segmento_id"],
                "segmento": nomes_seg.get(s["segmento_id"], s["segmento_id"] or "Sem segmento"),
                "total": s["total"] or 0,
                "qualificados": s["qualificados"] or 0,
                "contatados": s["contatados"] or 0,
                "engajados": s["engajados"] or 0,
                "fechados": s["fechados"] or 0,
                "media_pontuacao": int(s["media_pontuacao"] or 0),
                "taxa_resposta_pct": round(100 * (s["engajados"] or 0) / abordados, 1) if abordados else 0.0,
            }
        )
    return {
        "por_status": [{"status": st, "nome": nome, "qtd": contagem.get(st, 0)} for st, nome in STATUS],
        "taxa_por_segmento": taxas,
    }


# ── Não contatar ─────────────────────────────────────────────────────────────


@app.get("/api/nao-contatar")
def listar_nao_contatar():
    with db.sessao() as conn:
        return [db.row_para_dict(r) for r in conn.execute("SELECT * FROM nao_contatar ORDER BY id DESC")]


@app.post("/api/nao-contatar")
def adicionar_nao_contatar(body: NaoContatarIn):
    if not body.telefone and not body.email and not body.lead_id:
        raise HTTPException(400, "Informe telefone, e-mail ou lead_id.")
    with db.sessao() as conn:
        telefone, email, nome = body.telefone, body.email, body.nome
        if body.lead_id:
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (body.lead_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Lead não encontrado")
            telefone = telefone or row["telefone"]
            email = email or row["email"]
            nome = nome or row["nome"]
        conn.execute(
            "INSERT INTO nao_contatar (telefone, email, nome, motivo, criado_em) VALUES (?,?,?,?,?)",
            (telefone or "", (email or "").strip().lower(), nome or "", body.motivo or "", _agora()),
        )
        # Descarta todos os leads com o mesmo telefone ou e-mail (filiais, recoletas).
        digitos = digitos_telefone(telefone)
        afetados = 0
        for r in conn.execute("SELECT id, status, telefone, email FROM leads WHERE status != 'descartado'").fetchall():
            mesmo_tel = digitos and digitos_telefone(r["telefone"]) == digitos
            mesmo_email = email and (r["email"] or "").lower() == email.strip().lower()
            if mesmo_tel or mesmo_email or r["id"] == body.lead_id:
                conn.execute("UPDATE leads SET status = 'descartado', atualizado_em = ? WHERE id = ?", (_agora(), r["id"]))
                db.registrar_historico(conn, r["id"], "status", r["status"], "descartado", "nao_contatar")
                afetados += 1
    return {"ok": True, "leads_descartados": afetados}


@app.delete("/api/nao-contatar/{item_id}")
def remover_nao_contatar(item_id: int):
    with db.sessao() as conn:
        conn.execute("DELETE FROM nao_contatar WHERE id = ?", (item_id,))
    return {"ok": True}


# ── Exportação ───────────────────────────────────────────────────────────────


@app.get("/api/exportar")
def api_exportar(
    segmento_id: str | None = None,
    campanha_id: int | None = None,
    status: str | None = None,
    bairro: str | None = None,
    pontuacao_min: int | None = None,
    pontuacao_max: int | None = None,
    sem_site: bool = False,
    com_telefone: bool = False,
    q: str | None = None,
    ordem: str = "pontuacao",
):
    path = exportar_filtrado(
        _filtros(
            segmento_id=segmento_id, campanha_id=campanha_id, status=status,
            bairro=bairro, pontuacao_min=pontuacao_min, pontuacao_max=pontuacao_max,
            sem_site=sem_site, com_telefone=com_telefone, q=q, ordem=ordem,
        )
    )
    return FileResponse(path, filename=path.name, media_type="text/csv")


# ── Frontend ─────────────────────────────────────────────────────────────────

class StaticSemCache(StaticFiles):
    """Revalida JS/CSS a cada carga, para o navegador não misturar HTML novo com JS antigo."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", StaticSemCache(directory=str(STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
