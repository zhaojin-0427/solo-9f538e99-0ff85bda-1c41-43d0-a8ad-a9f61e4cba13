#!/usr/bin/env python3
"""交付前试跑：同单挪界、过期签收拒绝、等长挪段、消失停供、冲突整单退回。

用法：
    PYTHONPATH=/tmp/pylibs python3 scripts/trial.py
（会使用 /tmp/cadastre_trial.db，可重复运行，每次先删除旧库。）
"""
from __future__ import annotations

import json
import os
import sys

DB_FILE = "/tmp/cadastre_trial.db"
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(DB_FILE + suffix)
    except FileNotFoundError:
        pass
os.environ["CADASTRE_DB_PATH"] = DB_FILE
os.environ["SURVEY_AREA_NAME"] = "试跑测区"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def show(title, response):
    print(f"\n### {title}")
    print(f"HTTP {response.status_code}")
    try:
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except Exception:
        print(response.text)


def post_order(c, parcels, order_no=None):
    body = {"parcels": [{"parcel_no": n, "ring": r} for n, r in parcels]}
    if order_no:
        body["order_no"] = order_no
    return c.post("/api/orders", json=body)


def must(cond, msg):
    if not cond:
        print(f"\n断言失败: {msg}")
        sys.exit(1)
    print(f"  ✓ {msg}")


def main():
    with TestClient(app) as c:
        print("=" * 72)
        print("地籍共边服务试跑；/docs = http://127.0.0.1:8000/docs")
        print("=" * 72)
        show("健康检查", c.get("/health"))
        show("测区信息", c.get("/api/survey-area"))

        # 1) 首单：A、B 共边 x=10, y[0,10]
        r = post_order(
            c,
            [("A", rect(0, 0, 10, 10)), ("B", rect(10, 0, 20, 10))],
            "TRIAL-O1",
        )
        show("首单录入 A、B（TRIAL-O1）", r)
        must(r.status_code == 200, "首单整单生效")

        r = c.get("/api/shared-edges?parcel_a=B&parcel_b=A")  # 故意反序
        show("按宗地对(B,A)查共边（顺序无关）", r)
        edge = r.json()["edges"][0]
        eid = edge["edge_id"]
        must(r.json()["parcel_a"] == "A", "宗地对自动归一化为 A,B")
        must(edge["version"] == 1 and edge["sign_status"] == "unsigned", "共边 v1 待核验")

        # 2) 核验员凭所见 v1 签收
        r = c.post(f"/api/shared-edges/{eid}/sign", json={"verifier": "核验员-赵", "seen_version": 1})
        show("凭所见版本 v1 签收", r)
        sid_v1 = r.json()["current_signoff_id"]
        must(r.status_code == 200 and r.json()["sign_status"] == "signed", "v1 签收成功")

        r = c.get("/api/mapping/shared-boundaries")
        show("制图取数（签收后，应含 1 条）", r)
        must(r.json()["feature_count"] == 1, "制图取到 1 条已签收线")

        # 3) 同单挪界：A、B 在同一单内把共边 x=10 -> x=12（等长平移）
        r = post_order(
            c,
            [("A", rect(0, 0, 12, 10)), ("B", rect(12, 0, 20, 10))],
            "TRIAL-O2-MOVE",
        )
        show("同单挪界：A、B 共边移到 x=12（TRIAL-O2-MOVE）", r)
        must(r.status_code == 200, "挪界整单生效")
        must(len(r.json()["shared_edges"]["updated"]) == 1, "1 条共边按范围变动升版本")
        rb = r.json()["readback"]
        must(set(rb["candidate_parcels"]) == {"A", "B"}, "只回读相关宗地外环")

        r = c.get(f"/api/shared-edges?parcel_a=A&parcel_b=B")
        show("挪界后按宗地对查询（同 edge_id，v2，stale）", r)
        edge2 = r.json()["edges"][0]
        must(edge2["edge_id"] == eid and edge2["version"] == 2, "同一共边身份升到 v2")
        must(edge2["sign_status"] == "stale", "旧签收变为 stale，必须重验")
        must(edge2["geometry"]["coordinates"] == [[12, 0], [12, 10]], "图上范围确为新位置 x=12")

        # 4) 过期签收：仍按所见 v1 提交 -> 409
        r = c.post(f"/api/shared-edges/{eid}/sign", json={"verifier": "核验员-赵", "seen_version": 1})
        show("过期签收：用旧所见版本 v1 再签（应 409 拒绝）", r)
        must(r.status_code == 409 and r.json()["error"] == "stale_signoff", "过期签收被拒绝")

        r = c.get(f"/api/mapping/delivery/{sid_v1}")
        show("用 v1 旧签收凭证向制图供数（应 409 拒绝）", r)
        must(r.status_code == 409, "过期凭证供数被拒绝")

        r = c.get("/api/mapping/shared-boundaries")
        show("挪界后制图取数（应暂时为 0 条）", r)
        must(r.json()["feature_count"] == 0, "未重验前制图取不到该线")

        # 5) 按新所见 v2 签收，恢复供数
        r = c.post(f"/api/shared-edges/{eid}/sign", json={"verifier": "核验员-钱", "seen_version": 2})
        show("按最新所见 v2 重新签收", r)
        must(r.status_code == 200, "v2 签收成功")
        sid_v2 = r.json()["current_signoff_id"]
        r = c.get(f"/api/mapping/delivery/{sid_v2}")
        must(r.status_code == 200, "新凭证可供数")
        must(c.get("/api/mapping/shared-boundaries").json()["feature_count"] == 1, "制图恢复 1 条新线")

        # 6) 等长但范围沿向挪动 y[0,10] -> y[2,12]，仍须重验
        post_order(c, [("C", rect(40, 0, 50, 10)), ("D", rect(50, 0, 60, 10))], "TRIAL-O3")
        eid_cd = c.get("/api/shared-edges?parcel_a=C&parcel_b=D").json()["edges"][0]["edge_id"]
        c.post(f"/api/shared-edges/{eid_cd}/sign", json={"verifier": "w", "seen_version": 1})
        r = post_order(c, [("C", rect(40, 2, 50, 12)), ("D", rect(50, 2, 60, 12))], "TRIAL-O4-SHIFT")
        show("等长挪段：共边 y[0,10] -> y[2,12]", r)
        edge_cd = c.get("/api/shared-edges?parcel_a=C&parcel_b=D").json()["edges"][0]
        must(edge_cd["version"] == 2 and edge_cd["sign_status"] == "stale", "即使等长，范围变动也要求重验")

        # 7) 消失的共边停止供数
        post_order(c, [("E", rect(70, 0, 80, 10)), ("F", rect(80, 0, 90, 10))], "TRIAL-O5")
        eid_ef = c.get("/api/shared-edges?parcel_a=E&parcel_b=F").json()["edges"][0]["edge_id"]
        sid_ef = c.post(
            f"/api/shared-edges/{eid_ef}/sign", json={"verifier": "w", "seen_version": 1}
        ).json()["current_signoff_id"]
        post_order(c, [("E", rect(70, 0, 75, 10))], "TRIAL-O6-GONE")
        ef = c.get("/api/shared-edges?parcel_a=E&parcel_b=F").json()["edges"][0]
        must(ef["state"] == "removed", "共边消失后置 removed")
        must(c.get("/api/mapping/delivery/{0}".format(sid_ef)).status_code == 409, "消失线旧凭证供数被拒绝")

        # 8) 冲突：整单退回并报冲突编号，原状态不变
        r = post_order(
            c,
            [("A", rect(5, 0, 15, 10)), ("G", rect(200, 200, 210, 210))],
            "TRIAL-BAD-CONFLICT",
        )
        show("冲突单：A 压入 B 内部（应 409 整单退回）", r)
        must(r.status_code == 409, "冲突单被拒绝")
        must(set(r.json()["conflict_parcel_nos"]) == {"A", "B"}, "返回冲突宗地编号 A、B")
        a_ring = c.get("/api/parcels/A").json()["ring"]
        must([list(p) for p in a_ring] == rect(0, 0, 12, 10), "A 保持上一已应用单 O2 的图形")
        must(c.get("/api/parcels/G").status_code == 404, "同单新宗地 G 也未落库")
        must(c.get("/api/orders/TRIAL-BAD-CONFLICT").status_code == 404, "退回单不产生已应用单记录")

        print("\n" + "=" * 72)
        print("全部试跑断言通过。")
        print("=" * 72)


if __name__ == "__main__":
    main()
