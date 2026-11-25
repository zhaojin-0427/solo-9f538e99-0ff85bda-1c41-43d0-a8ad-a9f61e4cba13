# 交付说明（DELIVERY）

交付时间：2026-11-25。分支 `main`，基线提交 `3f7a0fe`，本次改动均为新增文件，
尚未提交 git（按约定需要你确认后再 commit）。

---

## 一、完成的功能（对照原始要求逐条）

| 原始要求 | 实现位置 | 说明 / 验证 |
| --- | --- | --- |
| FastAPI，`/docs` 入口 | `app/main.py` | `/docs` 为 Swagger UI，资源已下载自托管到 `app/static/vendor/`，离线浏览器也能开；另有 `/redoc`、`/openapi.json` |
| SQLite 存一个测区 | `app/db.py` | `survey_area` 为 `id=1` 单例行；一个库文件 = 一个测区，库路径由 `CADASTRE_DB_PATH` 控制 |
| 按宗地号录入 / 更正外环 | `POST /api/orders`、`PUT /api/parcels/{no}/ring` | upsert 语义；一单可多宗，宗地号单内去重 |
| 地块无洞 | `app/schemas.py` | 只建模 `ring`；`holes` 可显式传 `[]`，传非空内环直接 422 |
| 边与坐标轴平行 | `app/geometry.py::normalize_ring` | 逐边检查 `x` 相等或 `y` 相等，斜边 422 |
| ±100 万米内整数坐标 | `app/schemas.py` + `geometry.py` | pydantic `StrictInt`（`1.0`/`true` 不收），再做 `[-1e6,1e6]` 范围校验 |
| 以整单替换后的图形为准 | `service.apply_order` | 先在内存构造“本单新环 + 候选旧环”的替换后状态，再统一做冲突校验 |
| 内部相交退回整单、报冲突编号 | `409 parcel_interior_conflict` | 返回 `conflicts[].parcels` 与去重排序的 `conflict_parcel_nos`；同单无关新宗地也不落库，有测试 |
| 核验员按宗地对查线段、版本、签收状态 | `GET /api/shared-edges?parcel_a=&parcel_b=`、`GET /api/parcel-pairs/{a}/{b}` | 返回 GeoJSON 线段、`version`、`state`、`sign_status(signed/unsigned/stale/removed)`、签收人/时间/凭证 |
| 凭所见版本签收 | `POST /api/shared-edges/{id}/sign`、宗地对批量签收 | 必须带 `seen_version`（兼容字段名 `version`）；版本不符 `409 stale_signoff` 并回带当前版本与最新图形 |
| 单点接触不算共边 | `geometry.shared_segments` | 只保留重叠长度 `hi > lo`（严格正长度），有对点接触测试 |
| 范围有变即使等长也要重验 | 版本对账 `reconcile_pair` | 同 id 升 `version`、清空当前签收、状态变 `stale`；有“等长 y[0,10]→y[2,12]”专项测试 |
| 范围照旧保留签收 | 同上 | 几何键完全一致时数据库行不动，`version` 与 `current_signoff_id` 原样保留，有测试 |
| 消失的停止供数 | 旧线置 `state='removed'` | 制图 feed 过滤；旧签收凭证取数 409；removed 历史行保留可查 |
| 制图只给当前已签收的线 | `GET /api/mapping/shared-boundaries` | GeoJSON；SQL `JOIN signoffs` 且要求 `state='signed'`、凭证为当前凭证、签收版本=`e.version` |
| 过期签收须拒绝 | 签收接口 409 + `GET /api/mapping/delivery/{signoff_id}` 409 | 旧版本签收、旧凭证、removed 线三种情况均拒绝，有端到端测试 |
| 改界、共边、签收状态一起生效；写入失败照旧 | 单事务 `BEGIN IMMEDIATE` | 宗地 upsert、orders、edges 版本/状态、signoffs 同一事务；异常 rollback；有 monkeypatch 写故障回滚测试 |
| 只回读外接框与本单任一新、旧外接框相交的外环 | `geometry.plan_ring_reads` + `apply_order` | 先只扫 bbox 列，再 `IN (...)` 读候选 `ring_json`；响应 `readback` 给出候选与实际回读清单，有断言 FAR 宗地不回读 |
| 计算不逐米铺格 | `app/geometry.py` | 全部为整数线段相交、区间重叠/合并、射线法点在内；坐标跨度大小与耗时无关 |
| 查询共边不读原外环 | `service.pair_edges` | 只查 `shared_edges/signoffs`；有 SQL trace 测试断言请求 SQL 不含 `parcels` |
| docker compose 启动 | `Dockerfile`、`docker-compose.yml` | 卷持久化 `/data`，带 healthcheck；说明见下“实测边界” |
| 交付前核对改动、试跑、写明结果 | `tests/`、`scripts/trial.py`、本文件、README | 见下 |

### 接口额外补充（不影响主链路）

- `GET /` 导航、`GET /health`、`GET /api/survey-area`
- `GET /api/parcels`、`GET /api/parcels/{no}`、`GET /api/parcels/{no}/shared-edges`
- `GET /api/orders`、`GET /api/orders/{order_no}`（含每单回读与对账明细）
- `POST /api/parcel-pairs/{a}/{b}/sign` 宗地对批量原子签收

---

## 二、实测结果（均为本环境真实执行）

### 1. 单元 + 端到端测试：29/29 通过

命令：

```bash
PYTHONPATH=/tmp/pylibs python3 -m pytest -v
```

结果（末尾汇总）：

```
29 passed, 1 warning in 0.78s
```

覆盖：环规范化/共线合并、斜边/非整数/超范围/折回/自交拒绝、相邻共边、
单点不算共边、面积重叠、十字穿越、包含、完全重叠、T 接、部分重叠、L 形、
候选 bbox 规划，以及 11 个 API/事务场景（挪界+过期签收全时序、等长挪段、
保留签收、消失停供、冲突整单退回、坏环整单拒绝、回读范围、查询不读外环、
宗地对双线段批量签收原子性、写故障回滚、文档与静态资源）。

> 唯一 warning 是 starlette/httpx 测试客户端的弃用提示，与功能无关。

### 2. 叙述式试跑：25 条断言全部通过

```bash
PYTHONPATH=/tmp/pylibs python3 scripts/trial.py
# exit=0，末尾输出：全部试跑断言通过。
```

实测覆盖你点名的两件事：

- **同单挪界**：一单内同时把 `A=[0,0]-[10,10]` 扩到 x=12、`B` 缩到从 x=12 起，
  共边 `x=12,y[0,10]`（等长平移）。返回 `shared_edges.updated=[{edge_id:1,
  old_version:1, version:2}]`；同 id 查询为 `version=2 / sign_status=stale`。
- **过期签收**：仍按所见 `version=1` 调签收 → 真实响应：

```json
{
  "error": "stale_signoff",
  "message": "签收被拒绝：核验员所见版本 v1 已过期，当前版本为 v2，请按最新图形重新核验后签收",
  "edge_id": 1, "seen_version": 1, "current_version": 2
}
```

HTTP `409`；旧 `signoff_id` 调 `/api/mapping/delivery/{id}` 也是 `409`；
重签 v2 后 feed 恢复 1 条且坐标为 `[[12,0],[12,10]]`。

冲突单的真实 `409` 响应：

```json
{
  "error": "parcel_interior_conflict",
  "conflicts": [{"conflict_id": "C1", "parcels": ["A", "B"], "reason": "interior_overlap"}],
  "conflict_parcel_nos": ["A", "B"]
}
```

随后读 `A` 仍是上一单图形、同单新宗地 `G` 为 404、`GET /api/orders/TRIAL-BAD-CONFLICT` 为 404。

### 3. 真实 uvicorn + curl（与 Docker 同一启动模块）

```bash
CADASTRE_DB_PATH=/tmp/live_cadastre.db \
PYTHONPATH=/tmp/pylibs:/workspace \
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8123
```

实测状态：

```
GET /health                              -> {"status":"ok","database":"ok"}
GET /docs                                -> 200，<title>...Swagger UI</title>
POST /api/orders (HTTP-O1)               -> 200，edges_created 1
GET  /api/shared-edges?A&B               -> v1 unsigned [[10,0],[10,10]]
POST .../sign {"seen_version":1}         -> 200
POST /api/orders (同单挪界 HTTP-O2)       -> 200，updated [(1, 1, 2)]，readback=['A','B']
POST .../sign {"seen_version":1}         -> 409 stale_signoff seen 1 current 2
GET  /api/mapping/shared-boundaries      -> feature_count=0
POST .../sign {"seen_version":2}         -> 200
GET  /api/mapping/shared-boundaries      -> feature_count=1 coords=[[12,0],[12,10]]
```

自托管文档与宗地对批量签收在 8124 端口复验：

```
/docs 引用 /static/vendor/swagger-ui-bundle.js 与 swagger-ui.css
swagger-ui-bundle.js -> 200 application/javascript (1.8 MB)
swagger-ui.css       -> 200 text/css (206 KB)
POST /api/parcel-pairs/A/B/sign（两条边各 v1）-> signed_count=2，feed=2
```

### 4. Docker / compose 的实测边界（如实说明）

- 本沙箱 **没有 `docker` / `podman` / `nerdctl` 可执行文件**，因此
  `docker compose build` 与 `docker compose up` **未能在本环境真实执行**。
- 已完成的核对：`docker-compose.yml` 通过 PyYAML 解析，服务名/端口 `8000:8000`/
  命名卷 `/data`/healthcheck 结构正确；`Dockerfile` 使用 `python:3.12-slim`、
  `pip install -r requirements.txt`、复制 `app/`、启动命令
  `uvicorn app.main:app --host 0.0.0.0 --port 8000`，与上面实测通过的启动方式一致；
  `app/static/vendor` 会随 `COPY app ./app` 进入镜像。
- 目标机器具备 Docker 时的启动方式就是 `docker compose up --build`。

### 5. 依赖安装说明（本沙箱特殊情况）

系统 Python 3.11.2 未带 pip 且无 `ensurepip`，本次实测依赖是用 get-pip 引导到
`/tmp/pylibs`，再 `pip install --target /tmp/pylibs fastapi "uvicorn[standard]"
pytest httpx`（实际版本 fastapi 0.165.0 / pydantic 2.14.4 / pytest 9.2.0）。
仓库 `requirements.txt` / `requirements-dev.txt` 是标准安装方式，Docker 构建不受影响。

---

## 三、未完成事项与已知限制

1. **未实跑 Docker 构建**：原因如上（沙箱无容器运行时）；只做到配置解析 + 等价 uvicorn 实测。
2. **无身份认证 / 权限 / HTTPS**：任务未要求；`verifier` 只做非空记录，不校验核验员身份。
3. **不支持宗地删除 / 注销**：原文只要求“录入或更正外环”。“消失”指共边消失
   （`removed` 留痕并停供），不是删除宗地；宗地历史图形未做版本回溯，
   但每单的处理结果存在 `orders.result_json`，签收历史存在 `signoffs`。
4. **共边身份延续是启发式匹配**：规则为“同向 + 延伸区间严格正重叠 + 端点曼哈顿
   位移全局贪心最小”。极端的一对多 / 大范围重排可能被归类成 `removed + created`
   而非 `updated`；两种归类都不会错误保留旧签收（安全方向正确），差别仅在 edge_id 与展示。
5. **候选 bbox 扫描在 Python 侧做 O(n)**：只取外接框四列、不读外环，宗地量级在
   一个测区通常够用；若到数十万宗，可把相交条件下沉成 SQL 或加 R-tree 索引。
6. **并发模型面向单机适量写入**：SQLite + `BEGIN IMMEDIATE` 能串行化写事务，
   不适合多实例同时写同一个网络盘库；compose 当前是单副本 + 命名卷。
7. **`/redoc` 仍引用 CDN 资源**：只有任务点名的 `/docs` 做了完全自托管；
   若浏览器环境完全离线且需要 ReDoc，需要同样下载 redoc 资源。
8. **未做接口限流 / 分页**：`/api/orders` 最近 100 条；宗地与共边列表当前全量返回。

---

## 四、交付物清单

```
Dockerfile docker-compose.yml .dockerignore
requirements.txt requirements-dev.txt pytest.ini .gitignore
README.md DELIVERY.md
app/ (__init__, main, schemas, service, geometry, db, errors, config
      + static/vendor 自托管 Swagger UI)
scripts/trial.py
tests/ (conftest + 2 个测试文件，29 用例)
```

建议的验收命令：

```bash
# 方式 A：docker compose
docker compose up --build           # 然后访问 http://localhost:8000/docs

# 方式 B：本机
pip install -r requirements-dev.txt
pytest -q
python scripts/trial.py
uvicorn app.main:app --port 8000
```
