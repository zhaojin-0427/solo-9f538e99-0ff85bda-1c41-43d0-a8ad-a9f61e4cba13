"""FastAPI 应用入口。

启动：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
文档入口：
    http://localhost:8000/docs
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, service
from .db import connect, init_default_file
from .errors import AppError
from .schemas import DirectRingIn, OrderIn, PairSignIn, SignIn


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    init_default_file()
    yield


app = FastAPI(
    title="地籍共边核验与制图供数服务",
    version=__version__,
    description=(
        "一个 SQLite 测区；按宗地号整单录入 / 更正轴对齐整数坐标外环。\n\n"
        "- 整单替换后统一校验，宗地内部相交则整单退回并报冲突宗地号；\n"
        "- 按宗地对查询共边线段、版本与签收状态，凭所见版本乐观签收；\n"
        "- 范围照旧保留签收，范围变动升版本需重验，消失则停止供数；\n"
        "- 制图取数仅含当前有效签收线，过期签收 / 凭证一律拒绝；\n"
        "- 改界、共边与签收状态在同一 SQLite 事务内生效，失败整体回滚。"
    ),
    docs_url=None,  # 使用下方自托管 Swagger 资源的 /docs，离线浏览器也可打开
    redoc_url="/redoc",
    lifespan=lifespan,
)

_STATIC_VENDOR = Path(__file__).resolve().parent / "static" / "vendor"


@app.get("/docs", include_in_schema=False, tags=["基础"], summary="Swagger UI 文档入口")
async def custom_docs() -> Any:
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} - Swagger UI",
        swagger_js_url="/static/vendor/swagger-ui-bundle.js",
        swagger_css_url="/static/vendor/swagger-ui.css",
    )


@app.get("/static/vendor/swagger-ui-bundle.js", include_in_schema=False)
async def swagger_js() -> FileResponse:
    return FileResponse(_STATIC_VENDOR / "swagger-ui-bundle.js", media_type="application/javascript")


@app.get("/static/vendor/swagger-ui.css", include_in_schema=False)
async def swagger_css() -> FileResponse:
    return FileResponse(_STATIC_VENDOR / "swagger-ui.css", media_type="text/css")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_body())


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Depends(get_conn)


# ---------------------------------------------------------------- 基础入口

@app.get("/", tags=["基础"], summary="服务入口与接口导航")
def root() -> dict[str, Any]:
    return {
        "service": "cadastre-shared-boundary",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "guides": {
            "survey_area": "/api/survey-area",
            "submit_order": "POST /api/orders",
            "pair_shared_edges": "GET /api/shared-edges?parcel_a=A&parcel_b=B",
            "sign_edge": "POST /api/shared-edges/{edge_id}/sign",
            "mapping_feed": "/api/mapping/shared-boundaries",
            "signoff_delivery": "GET /api/mapping/delivery/{signoff_id}",
        },
    }


@app.get("/health", tags=["基础"], summary="健康检查（含 SQLite 连通性）")
def health(conn: sqlite3.Connection = Conn) -> dict[str, str]:
    conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "database": "ok"}


@app.get("/api/survey-area", tags=["测区"], summary="查看唯一测区与宗地数量")
def get_survey_area(conn: sqlite3.Connection = Conn) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM survey_area WHERE id=1").fetchone()
    parcel_count = conn.execute("SELECT COUNT(*) AS c FROM parcels").fetchone()["c"]
    edge_count = conn.execute(
        "SELECT COUNT(*) AS c FROM shared_edges WHERE state != 'removed'"
    ).fetchone()["c"]
    return {
        "id": row["id"],
        "name": row["name"],
        "coord_system": row["coord_system"],
        "coord_range_m": [row["min_coord"], row["max_coord"]],
        "created_at": row["created_at"],
        "parcel_count": parcel_count,
        "active_shared_edge_count": edge_count,
    }


# ---------------------------------------------------------------- 宗地查询

@app.get("/api/parcels", tags=["宗地"], summary="宗地列表（仅外接框，不含外环）")
def list_parcels(conn: sqlite3.Connection = Conn) -> dict[str, Any]:
    return service.list_parcels(conn)


@app.get("/api/parcels/{parcel_no}", tags=["宗地"], summary="按宗地号读外环与外接框")
def get_parcel(parcel_no: str, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
    return service.get_parcel(conn, parcel_no)


@app.put(
    "/api/parcels/{parcel_no}/ring",
    tags=["宗地"],
    summary="录入 / 更正单个宗地外环（按一宗地的整单生效）",
)
def put_parcel_ring(
    parcel_no: str, body: DirectRingIn, conn: sqlite3.Connection = Conn
) -> dict[str, Any]:
    order = OrderIn(
        order_no=body.order_no,
        parcels=[{"parcel_no": parcel_no, "ring": [list(p) for p in body.ring]}],
    )
    return service.apply_order(conn, order)


# ---------------------------------------------------------------- 整单改界

@app.post("/api/orders", tags=["整单改界"], summary="提交整单：多宗地外环整单替换、校验、共边对账")
def submit_order(body: OrderIn, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
    return service.apply_order(conn, body)


@app.get("/api/orders", tags=["整单改界"], summary="最近整单列表")
def list_orders(conn: sqlite3.Connection = Conn) -> dict[str, Any]:
    return service.list_orders(conn)


@app.get("/api/orders/{order_no}", tags=["整单改界"], summary="按单号查整单处理结果")
def get_order(order_no: str, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
    return service.get_order(conn, order_no)


# ---------------------------------------------------------------- 共边查询 / 签收

@app.get(
    "/api/shared-edges",
    tags=["共边核验"],
    summary="按宗地对查共边线段、版本与签收状态（不读外环）",
)
def get_pair_shared_edges(
    parcel_a: str = Query(..., description="宗地号 A（顺序无关，内部归一化）"),
    parcel_b: str = Query(..., description="宗地号 B"),
    include_removed: bool = Query(True, description="是否包含已消失(removed)的历史线"),
    conn: sqlite3.Connection = Conn,
) -> dict[str, Any]:
    return service.pair_edges(conn, parcel_a, parcel_b, include_removed=include_removed)


@app.get(
    "/api/parcel-pairs/{parcel_a}/{parcel_b}",
    tags=["共边核验"],
    summary="按宗地对查共边（路径形式别名，不读外环）",
)
def get_parcel_pair_edges_path(
    parcel_a: str,
    parcel_b: str,
    include_removed: bool = Query(True),
    conn: sqlite3.Connection = Conn,
) -> dict[str, Any]:
    return service.pair_edges(conn, parcel_a, parcel_b, include_removed=include_removed)


@app.post(
    "/api/parcel-pairs/{parcel_a}/{parcel_b}/sign",
    tags=["共边核验"],
    summary="按宗地对批量签收；任一所见版本过期则整批 409 回滚",
)
def sign_parcel_pair(
    parcel_a: str,
    parcel_b: str,
    body: PairSignIn,
    conn: sqlite3.Connection = Conn,
) -> dict[str, Any]:
    return service.sign_pair(
        conn,
        parcel_a,
        parcel_b,
        body.verifier,
        [item.model_dump() for item in body.items],
    )


@app.get(
    "/api/parcels/{parcel_no}/shared-edges",
    tags=["共边核验"],
    summary="查某宗地当前全部共边",
)
def get_parcel_shared_edges(
    parcel_no: str, conn: sqlite3.Connection = Conn
) -> dict[str, Any]:
    return service.parcel_edges(conn, parcel_no)


@app.post(
    "/api/shared-edges/{edge_id}/sign",
    tags=["共边核验"],
    summary="核验员凭所见版本签收；版本不符按过期签收拒绝(409)",
)
def sign_shared_edge(
    edge_id: int, body: SignIn, conn: sqlite3.Connection = Conn
) -> dict[str, Any]:
    return service.sign_edge(conn, edge_id, body.verifier, body.seen_version)


# ---------------------------------------------------------------- 制图取数

@app.get(
    "/api/mapping/shared-boundaries",
    tags=["制图取数"],
    summary="制图取数：仅当前版本且有效签收的共边（GeoJSON）",
)
def mapping_shared_boundaries(conn: sqlite3.Connection = Conn) -> dict[str, Any]:
    return service.mapping_feed(conn)


@app.get(
    "/api/mapping/delivery/{signoff_id}",
    tags=["制图取数"],
    summary="凭签收凭证取线；过期签收 / 消失线一律 409 拒绝",
)
def mapping_delivery(signoff_id: int, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
    return service.delivery_by_signoff(conn, signoff_id)
