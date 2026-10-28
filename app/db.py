"""SQLite local: leads, campanhas, histórico, lista não contatar."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.normalizacao import digitos_telefone

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("RADAR_DB") or ROOT / "data" / "radar.db")


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conectar() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def sessao():
    conn = conectar()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS campanhas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    segmento_id TEXT,
    termos TEXT NOT NULL,
    cidade TEXT NOT NULL,
    profundidade INTEGER DEFAULT 5,
    grid INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pendente',
    job_ids TEXT DEFAULT '[]',
    erro TEXT,
    bloqueio_suspeito INTEGER DEFAULT 0,
    linhas_coletadas INTEGER DEFAULT 0,
    criada_em TEXT NOT NULL,
    finalizada_em TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT,
    nome TEXT NOT NULL,
    telefone TEXT,
    email TEXT,
    site TEXT,
    categoria TEXT,
    endereco TEXT,
    bairro TEXT,
    nota REAL,
    avaliacoes INTEGER DEFAULT 0,
    instagram TEXT,
    facebook TEXT,
    linkedin TEXT,
    segmento_id TEXT,
    campanha_id INTEGER,
    pontuacao INTEGER DEFAULT 0,
    motivos TEXT DEFAULT '',
    enriquecimento TEXT DEFAULT '{}',
    status TEXT DEFAULT 'novo',
    responsavel TEXT,
    anotacoes TEXT DEFAULT '',
    proximo_passo TEXT,
    rascunho TEXT DEFAULT '',
    origem TEXT DEFAULT 'google_maps',
    coletado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE(place_id),
    FOREIGN KEY (campanha_id) REFERENCES campanhas(id)
);

CREATE TABLE IF NOT EXISTS historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    campo TEXT NOT NULL,
    valor_anterior TEXT,
    valor_novo TEXT,
    quem TEXT,
    em TEXT NOT NULL,
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS nao_contatar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telefone TEXT,
    email TEXT,
    nome TEXT,
    motivo TEXT,
    criado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_pontuacao ON leads(pontuacao DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_segmento ON leads(segmento_id);
CREATE INDEX IF NOT EXISTS idx_nao_telefone ON nao_contatar(telefone);
CREATE INDEX IF NOT EXISTS idx_nao_email ON nao_contatar(email);
"""

# Colunas adicionadas em versões posteriores. Bancos antigos recebem ALTER TABLE.
MIGRACOES = {
    "leads": {
        "tipo_site": "TEXT DEFAULT ''",
        "latitude": "REAL",
        "longitude": "REAL",
        "maps_url": "TEXT DEFAULT ''",
        "componentes": "TEXT DEFAULT '{}'",
    },
    "campanhas": {
        "fora_da_cidade": "INTEGER DEFAULT 0",
        "fechados": "INTEGER DEFAULT 0",
    },
}


def inicializar() -> None:
    with sessao() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for tabela, colunas in MIGRACOES.items():
            existentes = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})")}
            for nome, tipo in colunas.items():
                if nome not in existentes:
                    conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")


def registrar_historico(
    conn: sqlite3.Connection,
    lead_id: int,
    campo: str,
    anterior: str | None,
    novo: str | None,
    quem: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO historico (lead_id, campo, valor_anterior, valor_novo, quem, em) VALUES (?,?,?,?,?,?)",
        (lead_id, campo, anterior, novo, quem, _agora()),
    )


def esta_na_lista_nao_contatar(conn: sqlite3.Connection, telefone: str | None, email: str | None) -> bool:
    digitos = digitos_telefone(telefone)
    if digitos:
        rows = conn.execute("SELECT telefone FROM nao_contatar WHERE telefone IS NOT NULL AND telefone != ''").fetchall()
        if any(digitos_telefone(r["telefone"]) == digitos for r in rows):
            return True
    if email:
        em = email.strip().lower()
        row = conn.execute(
            "SELECT 1 FROM nao_contatar WHERE lower(email) = ? LIMIT 1", (em,)
        ).fetchone()
        if row:
            return True
    return False


def row_para_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for chave in ("enriquecimento", "job_ids", "termos", "componentes"):
        if chave in d and isinstance(d[chave], str):
            try:
                d[chave] = json.loads(d[chave])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


ORDENACOES = {
    "pontuacao": "pontuacao DESC, nome ASC",
    "nome": "nome COLLATE NOCASE ASC",
    "avaliacoes": "avaliacoes DESC, pontuacao DESC",
    "atualizado": "atualizado_em DESC",
    "coletado": "coletado_em DESC",
}


def filtros_leads(f: dict) -> tuple[str, list]:
    """WHERE compartilhado entre listagem, exportação e contagem."""
    sql = " WHERE 1=1"
    params: list = []
    if f.get("segmento_id"):
        sql += " AND segmento_id = ?"
        params.append(f["segmento_id"])
    if f.get("campanha_id"):
        sql += " AND campanha_id = ?"
        params.append(int(f["campanha_id"]))
    if f.get("status"):
        lista = [x for x in str(f["status"]).split(",") if x]
        sql += f" AND status IN ({','.join('?' * len(lista))})"
        params.extend(lista)
    if f.get("bairro"):
        sql += " AND bairro LIKE ?"
        params.append(f"%{f['bairro']}%")
    if f.get("pontuacao_min") is not None:
        sql += " AND pontuacao >= ?"
        params.append(int(f["pontuacao_min"]))
    if f.get("pontuacao_max") is not None:
        sql += " AND pontuacao <= ?"
        params.append(int(f["pontuacao_max"]))
    if f.get("sem_site"):
        sql += " AND (tipo_site IS NULL OR tipo_site != 'proprio' OR site = '')"
    if f.get("com_telefone"):
        sql += " AND telefone != ''"
    if f.get("q"):
        sql += " AND (nome LIKE ? OR endereco LIKE ? OR telefone LIKE ? OR categoria LIKE ? OR email LIKE ?)"
        params.extend([f"%{f['q']}%"] * 5)
    return sql, params
