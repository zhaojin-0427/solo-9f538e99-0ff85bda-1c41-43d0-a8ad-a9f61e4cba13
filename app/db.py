"""SQLite 连接与表结构。

一个 SQLite 库文件对应一个测区（``survey_area`` 为 id=1 的单例行）。
所有写事务由 service 层用 ``BEGIN IMMEDIATE`` 显式控制，保证
“改界 + 共边重算 + 签收状态变更”原子生效。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .config import db_path, survey_area_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS survey_area (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    name          TEXT NOT NULL,
    coord_system  TEXT NOT NULL DEFAULT 'PLANAR-INTEGER-METRE',
    min_coord     INTEGER NOT NULL DEFAULT -1000000,
    max_coord     INTEGER NOT NULL DEFAULT  1000000,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parcels (
    parcel_no   TEXT PRIMARY KEY,
    ring_json   TEXT NOT NULL,
    minx        INTEGER NOT NULL,
    miny        INTEGER NOT NULL,
    maxx        INTEGER NOT NULL,
    maxy        INTEGER NOT NULL,
    area2       INTEGER NOT NULL,
    order_id    INTEGER,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no    TEXT NOT NULL UNIQUE,
    item_count  INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- 当前共边线段。几何变化时原地升 version，消失置 state='removed'。
CREATE TABLE IF NOT EXISTS shared_edges (
    edge_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    parcel_a            TEXT NOT NULL,
    parcel_b            TEXT NOT NULL,
    orient              TEXT NOT NULL CHECK (orient IN ('h', 'v')),
    fixed               INTEGER NOT NULL,
    lo                  INTEGER NOT NULL,
    hi                  INTEGER NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    state               TEXT NOT NULL DEFAULT 'unsigned'
                            CHECK (state IN ('signed', 'unsigned', 'removed')),
    signed_by           TEXT,
    signed_at           TEXT,
    current_signoff_id INTEGER,
    created_order_id    INTEGER,
    updated_order_id    INTEGER,
    updated_at          TEXT NOT NULL
);

-- 同一几何键只允许一条“当前”共边；removed 历史行不阻塞新线段。
CREATE UNIQUE INDEX IF NOT EXISTS ux_edges_active_geom
    ON shared_edges(parcel_a, parcel_b, orient, fixed, lo, hi)
    WHERE state <> 'removed';

CREATE INDEX IF NOT EXISTS idx_edges_pair
    ON shared_edges(parcel_a, parcel_b);

CREATE INDEX IF NOT EXISTS idx_edges_state
    ON shared_edges(state);

CREATE TABLE IF NOT EXISTS signoffs (
    signoff_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id     INTEGER NOT NULL REFERENCES shared_edges(edge_id),
    version     INTEGER NOT NULL,
    verifier    TEXT NOT NULL,
    signed_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signoffs_edge ON signoffs(edge_id);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    """新建一个 autocommit 模式连接，事务边界由 service 显式管理。"""
    path = db_path()
    if path != ":memory:":
        # 延迟导入，避免 config 循环
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT id FROM survey_area WHERE id = 1").fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO survey_area (id, name, coord_system, min_coord, max_coord, created_at)
               VALUES (1, ?, 'PLANAR-INTEGER-METRE', -1000000, 1000000, ?)""",
            (survey_area_name(), utcnow_iso()),
        )


def init_default_file() -> None:
    """供应用启动时调用：确保默认库文件与表结构存在。"""
    conn = connect()
    try:
        init_db(conn)
    finally:
        conn.close()
