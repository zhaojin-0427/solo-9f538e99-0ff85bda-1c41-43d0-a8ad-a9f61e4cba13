"""应用配置。

数据库路径由环境变量 ``CADASTRE_DB_PATH`` 控制：
- 本机直接运行默认使用仓库下 ``data/cadastre.db``；
- Docker / compose 中指向 ``/data/cadastre.db`` 数据卷；
- 测试指向临时文件。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MIN_COORD = -1_000_000
MAX_COORD = 1_000_000

DEFAULT_SURVEY_AREA_NAME = "默认测区"


def db_path() -> str:
    """每次调用都读取环境变量，方便测试在导入后切换库文件。"""
    return os.environ.get("CADASTRE_DB_PATH", str(REPO_ROOT / "data" / "cadastre.db"))


def survey_area_name() -> str:
    return os.environ.get("SURVEY_AREA_NAME", DEFAULT_SURVEY_AREA_NAME)
