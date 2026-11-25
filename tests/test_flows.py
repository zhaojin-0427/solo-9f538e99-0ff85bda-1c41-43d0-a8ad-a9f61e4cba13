"""端到端业务场景测试。"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import connect
from app.schemas import OrderIn
from app import service


def rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def post_order(client, parcels, order_no=None):
    body = {"parcels": [{"parcel_no": n, "ring": r} for n, r in parcels]}
    if order_no:
        body["order_no"] = order_no
    return client.post("/api/orders", json=body)


def pair_edge(client, a="A", b="B"):
    r = client.get(f"/api/shared-edges?parcel_a={a}&parcel_b={b}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["edge_count"] == 1
    return data["edges"][0]


# ---------------------------------------------------------------- 基础

def test_health_docs_and_survey_area(client):
    assert client.get("/health").json()["status"] == "ok"
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()
    assert "/static/vendor/swagger-ui-bundle.js" in docs.text
    assert client.get("/static/vendor/swagger-ui-bundle.js").status_code == 200
    assert client.get("/static/vendor/swagger-ui.css").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    area = client.get("/api/survey-area").json()
    assert area["id"] == 1
    assert area["coord_range_m"] == [-1_000_000, 1_000_000]


# ---------------------------------------------------------------- 主流程

def test_same_order_boundary_move_and_expired_signoff(client):
    # 首单：A、B 共边 x=10, y[0,10]
    r = post_order(
        client,
        [("A", rect(0, 0, 10, 10)), ("B", rect(10, 0, 20, 10))],
        order_no="O1",
    )
    assert r.status_code == 200, r.text

    edge = pair_edge(client)
    eid = edge["edge_id"]
    assert edge["version"] == 1
    assert edge["sign_status"] == "unsigned"
    assert edge["geometry"]["coordinates"] == [[10, 0], [10, 10]]

    # 未见 v1 直接用 v2 签收应被拒
    bad = client.post(f"/api/shared-edges/{eid}/sign", json={"verifier": "张三", "seen_version": 2})
    assert bad.status_code == 409
    assert bad.json()["error"] == "stale_signoff"
    assert bad.json()["current_version"] == 1

    # 凭所见 v1 签收
    ok = client.post(f"/api/shared-edges/{eid}/sign", json={"verifier": "张三", "seen_version": 1})
    assert ok.status_code == 200, ok.text
    sid_v1 = ok.json()["current_signoff_id"]
    assert ok.json()["sign_status"] == "signed"

    feed = client.get("/api/mapping/shared-boundaries").json()
    assert feed["feature_count"] == 1
    delivery = client.get(f"/api/mapping/delivery/{sid_v1}")
    assert delivery.status_code == 200

    # 第二单：A、B 同单更正，共边整体平移 x=10 -> x=12（长度不变）
    r = post_order(
        client,
        [("A", rect(0, 0, 12, 10)), ("B", rect(12, 0, 20, 10))],
        order_no="O2",
    )
    assert r.status_code == 200, r.text
    moved = r.json()["shared_edges"]["updated"]
    assert len(moved) == 1
    assert moved[0]["edge_id"] == eid
    assert moved[0]["version"] == 2

    # 查询：同一 edge_id，新版本，旧签收状态为 stale
    edge2 = pair_edge(client)
    assert edge2["edge_id"] == eid
    assert edge2["version"] == 2
    assert edge2["sign_status"] == "stale"
    assert edge2["geometry"]["coordinates"] == [[12, 0], [12, 10]]

    # 过期签收（拿旧所见版本 1）必须拒绝
    stale = client.post(
        f"/api/shared-edges/{eid}/sign", json={"verifier": "李四", "seen_version": 1}
    )
    assert stale.status_code == 409
    assert stale.json()["error"] == "stale_signoff"
    assert stale.json()["current_version"] == 2

    # 旧签收凭证取数必须拒绝；制图 feed 不再供应该线
    old_delivery = client.get(f"/api/mapping/delivery/{sid_v1}")
    assert old_delivery.status_code == 409
    assert client.get("/api/mapping/shared-boundaries").json()["feature_count"] == 0

    # 按所见 v2 签收后恢复供数，图形为新范围
    ok2 = client.post(
        f"/api/shared-edges/{eid}/sign", json={"verifier": "李四", "seen_version": 2}
    )
    assert ok2.status_code == 200, ok2.text
    sid_v2 = ok2.json()["current_signoff_id"]
    feed2 = client.get("/api/mapping/shared-boundaries").json()
    assert feed2["feature_count"] == 1
    coords = feed2["features"][0]["geometry"]["coordinates"]
    assert coords == [[12, 0], [12, 10]]
    assert feed2["features"][0]["properties"]["signoff_id"] == sid_v2
    assert client.get(f"/api/mapping/delivery/{sid_v2}").status_code == 200
    # 旧凭证仍然拒绝
    assert client.get(f"/api/mapping/delivery/{sid_v1}").status_code == 409


def test_equal_length_but_shifted_range_requires_reverify(client):
    post_order(client, [("C", rect(0, 0, 10, 10)), ("D", rect(10, 0, 20, 10))])
    edge = pair_edge(client, "C", "D")
    eid = edge["edge_id"]
    assert (edge["lo"], edge["hi"], edge["length"]) == (0, 10, 10)
    client.post(f"/api/shared-edges/{eid}/sign", json={"verifier": "w", "seen_version": 1})

    # 共边沿同一条 x=10 线从 y[0,10] 挪到 y[2,12]，仍长 10 米
    r = post_order(client, [("C", rect(0, 2, 10, 12)), ("D", rect(10, 2, 20, 12))])
    assert r.status_code == 200, r.text
    edge2 = pair_edge(client, "C", "D")
    assert edge2["edge_id"] == eid
    assert edge2["version"] == 2
    assert (edge2["lo"], edge2["hi"], edge2["length"]) == (2, 12, 10)
    assert edge2["sign_status"] == "stale"
    stale = client.post(
        f"/api/shared-edges/{eid}/sign", json={"verifier": "w", "seen_version": 1}
    )
    assert stale.status_code == 409


def test_unchanged_range_keeps_signoff(client):
    post_order(client, [("A", rect(0, 0, 10, 10)), ("B", rect(10, 0, 20, 10))])
    eid = pair_edge(client)["edge_id"]
    sid = client.post(
        f"/api/shared-edges/{eid}/sign", json={"verifier": "王五", "seen_version": 1}
    ).json()["current_signoff_id"]

    # A 只改另一侧（向西扩），与 B 的共边范围 x=10,y[0,10] 照旧
    r = post_order(client, [("A", rect(-2, 0, 10, 10))], order_no="KEEP")
    assert r.status_code == 200, r.text
    assert len(r.json()["shared_edges"]["unchanged"]) == 1
    edge = pair_edge(client)
    assert edge["version"] == 1
    assert edge["sign_status"] == "signed"
    assert edge["current_signoff_id"] == sid
    feed = client.get("/api/mapping/shared-boundaries").json()
    assert feed["feature_count"] == 1
    assert feed["features"][0]["properties"]["signoff_id"] == sid


def test_disappeared_edge_stops_supply(client):
    post_order(client, [("A", rect(0, 0, 10, 10)), ("B", rect(10, 0, 20, 10))])
    eid = pair_edge(client)["edge_id"]
    sid = client.post(
        f"/api/shared-edges/{eid}/sign", json={"verifier": "z", "seen_version": 1}
    ).json()["current_signoff_id"]

    # A 缩回到 x<=5，与 B 之间出现间隙，共边消失
    r = post_order(client, [("A", rect(0, 0, 5, 10))], order_no="GONE")
    assert r.status_code == 200, r.text
    assert len(r.json()["shared_edges"]["removed"]) == 1

    data = client.get("/api/shared-edges?parcel_a=A&parcel_b=B").json()
    assert data["edge_count"] == 0
    assert data["edges"][0]["state"] == "removed"
    assert data["edges"][0]["sign_status"] == "removed"

    active = client.get("/api/shared-edges?parcel_a=A&parcel_b=B&include_removed=false").json()
    assert active["edges"] == []

    assert client.get("/api/mapping/shared-boundaries").json()["feature_count"] == 0
    gone_delivery = client.get(f"/api/mapping/delivery/{sid}")
    assert gone_delivery.status_code == 409
    # 已消失的线不能再签收
    sign_gone = client.post(
        f"/api/shared-edges/{eid}/sign", json={"verifier": "z", "seen_version": 1}
    )
    assert sign_gone.status_code == 409


# ---------------------------------------------------------------- 冲突整单退回

def test_interior_conflict_rejects_whole_order_and_reports_numbers(client):
    post_order(
        client,
        [("A", rect(0, 0, 10, 10)), ("B", rect(10, 0, 20, 10))],
        order_no="BASE",
    )
    eid = pair_edge(client)["edge_id"]
    client.post(f"/api/shared-edges/{eid}/sign", json={"verifier": "v", "seen_version": 1})

    # 同单里 A 越界压入 B 内部，另带一宗新地 C；整单都不得生效
    r = post_order(
        client,
        [
            ("A", rect(5, 0, 15, 10)),
            ("C", rect(30, 30, 40, 40)),
        ],
        order_no="BAD",
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "parcel_interior_conflict"
    assert set(body["conflict_parcel_nos"]) == {"A", "B"}
    assert body["conflicts"][0]["parcels"] == ["A", "B"]

    # A 仍是旧环；C 未录入；坏单查不到；旧签收仍有效供数
    a = client.get("/api/parcels/A").json()
    assert a["ring"] == rect(0, 0, 10, 10)
    assert client.get("/api/parcels/C").status_code == 404
    assert client.get("/api/orders/BAD").status_code == 404
    assert client.get("/api/mapping/shared-boundaries").json()["feature_count"] == 1


def test_invalid_ring_in_order_rejects_entire_order(client):
    r = post_order(
        client,
        [("A", [[0, 0], [10, 0], [11, 11], [0, 10]]), ("B", rect(10, 0, 20, 10))],
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_ring"
    assert client.get("/api/parcels/A").status_code == 404
    assert client.get("/api/parcels/B").status_code == 404


# ---------------------------------------------------------------- 回读范围

def test_only_bbox_intersecting_rings_are_read_back(client):
    parcels = [
        ("A", rect(0, 0, 10, 10)),
        ("R", rect(10, 0, 20, 10)),
        ("L", rect(-10, 0, 0, 10)),
        ("FAR", rect(100, 100, 110, 110)),
    ]
    assert post_order(client, parcels, order_no="GRID").status_code == 200

    # A 向南扩 1 米：新/旧外接框只会交上 L、R
    r = post_order(client, [("A", rect(0, -1, 10, 10))], order_no="MOVE")
    assert r.status_code == 200, r.text
    rb = r.json()["readback"]
    assert set(rb["candidate_parcels"]) == {"A", "L", "R"}
    assert "FAR" not in rb["candidate_parcels"]
    assert set(rb["rings_read_from_db"]) == {"L", "R"}
    assert rb["ring_read_count"] == 2


# ---------------------------------------------------------------- 查询不读外环

def test_pair_edge_query_does_not_read_parcel_rings(client):
    post_order(client, [("A", rect(0, 0, 10, 10)), ("B", rect(10, 0, 20, 10))])

    sqls: list[str] = []

    def override_conn():
        # 在请求工作线程内建连，避免 sqlite 跨线程使用
        traced = connect()
        traced.set_trace_callback(lambda sql: sqls.append(sql))
        try:
            yield traced
        finally:
            traced.close()

    from app.main import app, get_conn

    app.dependency_overrides[get_conn] = override_conn
    try:
        resp = client.get("/api/shared-edges?parcel_a=A&parcel_b=B")
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()

    assert sqls, "应当确实执行了 SQL"
    assert not any("parcels" in s.lower() for s in sqls), sqls


# ---------------------------------------------------------------- 写入失败回滚

def test_pair_path_alias_and_batch_sign_is_atomic(client):
    # A 为大矩形，B 为底部带缺口的多边形，与 A 顶边有两段互不相连的共边
    a = rect(0, 0, 20, 10)
    b = [[2, 10], [4, 10], [4, 12], [16, 12], [16, 10], [18, 10], [18, 14], [2, 14]]
    assert post_order(client, [("A", a), ("B", b)]).status_code == 200

    path_resp = client.get("/api/parcel-pairs/A/B")
    assert path_resp.status_code == 200
    edges = path_resp.json()["edges"]
    assert [(e["orient_code"], e["lo"], e["hi"]) for e in edges] == [
        ("h", 2, 4),
        ("h", 16, 18),
    ]
    e1, e2 = edges[0]["edge_id"], edges[1]["edge_id"]

    # 第二批里第二个版本号错误 -> 整批 409，第一条也不得签收
    bad = client.post(
        "/api/parcel-pairs/A/B/sign",
        json={"verifier": "组", "items": [
            {"edge_id": e1, "seen_version": 1},
            {"edge_id": e2, "seen_version": 99},
        ]},
    )
    assert bad.status_code == 409
    assert client.get("/api/mapping/shared-boundaries").json()["feature_count"] == 0
    assert client.get("/api/parcel-pairs/A/B").json()["edges"][0]["sign_status"] == "unsigned"

    ok = client.post(
        "/api/parcel-pairs/A/B/sign",
        json={"verifier": "组", "items": [
            {"edge_id": e1, "seen_version": 1},
            {"edge_id": e2, "seen_version": 1},
        ]},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["signed_count"] == 2
    assert client.get("/api/mapping/shared-boundaries").json()["feature_count"] == 2


def test_write_failure_keeps_previous_state(db, monkeypatch):
    order1 = OrderIn(parcels=[
        {"parcel_no": "A", "ring": rect(0, 0, 10, 10)},
        {"parcel_no": "B", "ring": rect(10, 0, 20, 10)},
    ])
    service.apply_order(db, order1)
    eid = db.execute("SELECT edge_id FROM shared_edges").fetchone()["edge_id"]
    service.sign_edge(db, eid, "验员", 1)

    # 在写库后的共边计算阶段注入故障
    def boom(*a, **k):
        raise RuntimeError("simulated disk write failure")

    monkeypatch.setattr(service, "shared_segments", boom)

    order2 = OrderIn(order_no="CRASH", parcels=[
        {"parcel_no": "A", "ring": rect(0, 0, 12, 10)},
        {"parcel_no": "B", "ring": rect(12, 0, 20, 10)},
    ])
    with pytest.raises(RuntimeError):
        service.apply_order(db, order2)

    check = connect()
    try:
        import json

        a_ring = json.loads(
            check.execute("SELECT ring_json FROM parcels WHERE parcel_no='A'").fetchone()[0]
        )
        assert [list(p) for p in a_ring] == rect(0, 0, 10, 10)
        assert check.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 1
        edge = check.execute("SELECT version,state,signed_by FROM shared_edges WHERE edge_id=?", (eid,)).fetchone()
        assert edge["version"] == 1
        assert edge["state"] == "signed"
        assert edge["signed_by"] == "验员"
    finally:
        check.close()
