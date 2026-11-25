"""pytest 公共夹具：每个测试使用独立临时 SQLite 文件并清库。"""
from __future__ import annotations

import os
import tempfile

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="cadastre_pytest_")
os.environ["CADASTRE_DB_PATH"] = os.path.join(_TMP_DIR, "pytest.db")
os.environ["SURVEY_AREA_NAME"] = "测试测区"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402

_TABLES = ["signoffs", "shared_edges", "parcels", "orders", "survey_area"]


def _reset_database() -> None:
    conn = connect()
    try:
        from app.db import init_db

        init_db(conn)  # 全新临时文件上先确保表结构存在
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('orders','shared_edges','signoffs')"
        )
        conn.execute("PRAGMA foreign_keys = ON")
        # 重新写入测区单例行
        init_db(conn)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_db():
    _reset_database()
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
