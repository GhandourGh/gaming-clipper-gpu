"""Postgres access: jobs, per-stage status (resumable), clips, costs, events."""
import json
import logging
import sys
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

SCHEMA = """
create table if not exists jobs (
  id uuid primary key, idem_key text unique not null, source_type text not null,
  source_url text, source_path text, title text, status text not null default 'created',
  meta jsonb not null default '{}', error text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now());
create table if not exists stages (
  job_id uuid not null references jobs(id) on delete cascade, name text not null,
  clip_idx int not null default -1, status text not null default 'queued',
  attempts int not null default 0, started_at timestamptz, finished_at timestamptz,
  error text, result jsonb, primary key (job_id, name, clip_idx));
create table if not exists clips (
  job_id uuid not null references jobs(id) on delete cascade, idx int not null,
  candidate jsonb not null, edl jsonb, assets jsonb, status text not null default 'selected',
  review text not null default 'pending', review_note text, output_path text, qc jsonb,
  primary key (job_id, idx));
alter table if exists clips add column if not exists style text;
alter table if exists clips add column if not exists versions jsonb not null default '[]';
alter table if exists clips add column if not exists ai_review jsonb;
alter table if exists clips add column if not exists render_engine text;
alter table if exists clips add column if not exists encoder text;
alter table if exists clips add column if not exists render_seconds real;
alter table if exists clips add column if not exists render_note text;
alter table if exists clips add column if not exists fail_kind text;
create table if not exists costs (
  id bigserial primary key, job_id uuid not null references jobs(id) on delete cascade,
  item text not null, units jsonb, usd numeric(12,6) not null default 0, ts timestamptz not null default now());
create table if not exists events (
  id bigserial primary key, job_id uuid, ts timestamptz not null default now(),
  level text not null, stage text, msg text not null, data jsonb);
"""

_pool: ConnectionPool | None = None
log = logging.getLogger("clipper")


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.DB_DSN, min_size=1, max_size=5, kwargs={"row_factory": dict_row})
        with _pool.connection() as c:
            c.execute(SCHEMA)
    return _pool


@contextmanager
def conn():
    with pool().connection() as c:
        yield c


def q(sql, params=None):
    with conn() as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def q1(sql, params=None):
    r = q(sql, params)
    return r[0] if r else None


def J(v):
    return Jsonb(v)


def event(job_id, level, stage, msg, data=None):
    """Structured log: one JSON line on stdout + a row in events."""
    rec = {"job": str(job_id) if job_id else None, "level": level, "stage": stage, "msg": msg, "data": data}
    print(json.dumps(rec, default=str), file=sys.stdout, flush=True)
    try:
        q("insert into events(job_id, level, stage, msg, data) values (%s,%s,%s,%s,%s)",
          (job_id, level, stage, msg, J(data) if data is not None else None))
    except Exception:  # logging must never break the pipeline
        log.exception("event insert failed")


def add_cost(job_id, item, units, usd):
    q("insert into costs(job_id,item,units,usd) values (%s,%s,%s,%s)", (job_id, item, J(units), usd))


def set_job(job_id, **kw):
    sets = ", ".join(f"{k}=%s" for k in kw)
    vals = [J(v) if isinstance(v, (dict, list)) else v for v in kw.values()]
    q(f"update jobs set {sets}, updated_at=now() where id=%s", (*vals, job_id))
