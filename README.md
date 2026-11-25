# 地籍共边核验与制图供数服务

制图组从地籍库取“验收通过（已签收）”的共边。几宗地同单改界时，核验员只需重验
图上范围发生变化的线：范围照旧 → 保留签收；范围变动（即使等长）→ 版本 +1、
旧签收过期、必须重验；共边消失 → 标记 `removed` 并停止供数。

- FastAPI + 自带 Swagger 文档：<http://localhost:8000/docs>（Swagger 静态资源已自托管，离线浏览器可用）
- SQLite 单文件 = 一个测区（`survey_area` 单例行）
- 地块只有外环、无洞；边与坐标轴平行；顶点为 ±100 万米内整数
- 全部几何判定为整数线段 / 区间运算，**不逐米铺格**
- “改界 + 共边重算 + 签收状态变更”在同一个 `BEGIN IMMEDIATE` 事务内提交，失败整体回滚

---

## 1. docker compose 启动（推荐）

```bash
docker compose up --build
# 打开 http://localhost:8000/docs
```

- 容器内库路径 `/data/cadastre.db`，通过 compose 卷 `cadastre-data` 持久化。
- 健康检查：`GET http://localhost:8000/health`。
- 端口冲突时改 `docker-compose.yml` 的 `ports`（例如 `"18000:8000"`）。

常用命令：

```bash
docker compose up -d --build      # 后台启动
docker compose logs -f cadastre-api
docker compose down               # 停止（数据卷保留）
docker compose down -v            # 连同数据库卷一起删除
```

## 2. 本机直接运行

需要 Python 3.11+：

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试与试跑：

```bash
pip install -r requirements-dev.txt
pytest -q
python scripts/trial.py           # 端到端叙述式试跑，自带 25 条断言
```

数据库路径可用环境变量 `CADASTRE_DB_PATH` 覆盖（默认仓库下 `data/cadastre.db`），
测区名用 `SURVEY_AREA_NAME` 覆盖。

---

## 3. 典型业务时序

### 3.1 提交整单（录入 / 更正外环）

`POST /api/orders`：一单可含多宗地，**按整单替换后的图形统一校验**。

```bash
curl -X POST http://localhost:8000/api/orders \
  -H 'Content-Type: application/json' \
  -d '{
    "order_no": "GD-0001",
    "parcels": [
      {"parcel_no": "A", "ring": [[0,0],[10,0],[10,10],[0,10]]},
      {"parcel_no": "B", "ring": [[10,0],[20,0],[20,10],[10,10]]}
    ]
  }'
```

- 首尾点不必重复；允许共线冗余顶点（会自动合并）。
- `holes` 若给出必须为空数组（地块无洞）。
- 响应 `readback` 说明本单实际回读了哪些库内宗地外环；`shared_edges` 给出
  `created / updated / unchanged / removed` 四类对账结果。

若整单替换后有宗地内部相交（正面积重叠），返回 `409`，**整单退回**：

```json
{
  "error": "parcel_interior_conflict",
  "conflicts": [{"conflict_id": "C1", "parcels": ["A", "B"], "reason": "interior_overlap"}],
  "conflict_parcel_nos": ["A", "B"]
}
```

此时本单任何宗地（包括没有问题的新宗地）都不会落库，旧签收与供数不变。
环本身不合法（斜边、非整数、超范围、自交、退化等）返回 `422 invalid_ring`。

### 3.2 核验员按宗地对查询

```bash
curl 'http://localhost:8000/api/shared-edges?parcel_a=A&parcel_b=B'
# 或路径形式 /api/parcel-pairs/A/B
```

每条线包含：

| 字段 | 含义 |
| --- | --- |
| `edge_id` | 共边稳定身份；范围变动时尽量保持同一 id 并升 `version` |
| `geometry` | GeoJSON `LineString`（轴对齐，两点端点） |
| `orient_code` / `fixed_coord` / `lo` / `hi` / `length` | `h`: y=fixed, x∈[lo,hi]；`v`: x=fixed, y∈[lo,hi] |
| `version` | 共边范围版本，从 1 开始 |
| `state` | `unsigned` / `signed` / `removed` |
| `sign_status` | `unsigned`（从未签）/ `signed`（当前版本已签）/ `stale`（旧版本签过，已过期需重验）/ `removed` |
| `current_signoff_id` / `signed_by` / `signed_at` | 当前有效签收信息 |

只有重叠长度严格大于 0 才算共边；仅端点相碰返回 0 条。

### 3.3 凭所见版本签收

```bash
curl -X POST http://localhost:8000/api/shared-edges/1/sign \
  -H 'Content-Type: application/json' \
  -d '{"verifier": "核验员-赵", "seen_version": 1}'
```

- `seen_version` 必须等于当前 `version`，否则 `409 stale_signoff`，并回带
  `current_version` 与最新线图形 —— 这就是“过期签收须拒绝”。
- 宗地对批量签收：`POST /api/parcel-pairs/A/B/sign`，体为
  `{"verifier":"组","items":[{"edge_id":1,"seen_version":1}, ...]}`；
  任一版本不符，整批回滚。

### 3.4 改界后的版本规则

- **范围完全相同**：数据库行不动，`version` 与签收凭证原样保留。
- **范围变动**（同向且延伸区间有正长度重叠，含整体平移、伸缩、等长挪段）：
  同一 `edge_id` 升一个版本，清空当前签收，`sign_status=stale`。
- **对不上的旧线**：置 `removed`，保留历史行，制图不再供应。
- **对不上的新线**：新 `edge_id`、`version=1`、待核验。

### 3.5 制图取数

```bash
curl http://localhost:8000/api/mapping/shared-boundaries
```

返回 GeoJSON `FeatureCollection`，**只含当前版本且签收凭证有效的线**；
过期 / 消失线被 SQL 连接条件直接过滤。

每张签收凭证还可单独验真取数：

```bash
curl http://localhost:8000/api/mapping/delivery/1     # 过期/消失 -> 409
```

---

## 4. API 一览

| 方法 & 路径 | 说明 |
| --- | --- |
| `GET /docs` | Swagger UI（自托管资源） |
| `GET /health` | 健康检查（含 SQLite `SELECT 1`） |
| `GET /api/survey-area` | 唯一测区、坐标范围、宗地 / 共边计数 |
| `POST /api/orders` | 提交整单（核心入口） |
| `GET /api/orders` / `GET /api/orders/{order_no}` | 整单列表 / 处理结果（含回读与对账明细） |
| `GET /api/parcels` / `GET /api/parcels/{no}` | 宗地列表（仅外接框）/ 读外环 |
| `PUT /api/parcels/{no}/ring` | 单宗地录入 / 更正（按一宗地的整单生效） |
| `GET /api/shared-edges?parcel_a=&parcel_b=` | 按宗地对查共边、版本、签收状态（**不读外环**） |
| `GET /api/parcel-pairs/{a}/{b}` | 同上，路径别名 |
| `GET /api/parcels/{no}/shared-edges` | 某宗地全部当前共边 |
| `POST /api/shared-edges/{id}/sign` | 凭所见版本签收单条线 |
| `POST /api/parcel-pairs/{a}/{b}/sign` | 按宗地对批量原子签收 |
| `GET /api/mapping/shared-boundaries` | 制图取数：当前有效签收线 GeoJSON |
| `GET /api/mapping/delivery/{signoff_id}` | 凭签收凭证取线，过期签收 409 |

---

## 5. 关键实现说明

### 5.1 只回读相交宗地的外环

`app/geometry.py::plan_ring_reads` + `app/service.py::apply_order`：

1. 先只扫描 `parcels` 的外接框四列（不碰 `ring_json`）；
2. 候选集 = 本单宗地号 ∪ 外接框与“本单任一新外接框 **或** 旧外接框”相交的宗地；
3. 只用一条 `... WHERE parcel_no IN (...)` 回读候选宗地的外环；
4. 响应 `readback.candidate_parcels / rings_read_from_db` 可核对本次实际回读范围。

旧外接框也参与筛选，保证“缩退 / 消失”场景下老邻居仍被回读并清理共边。

### 5.2 不铺格的几何

- 环校验：整数坐标范围、逐边轴对齐、共线合并、原路折回 / 自交拒绝、非零面积、统一 CCW。
- 内部相交：外接框快速排除 → 顶点严格入内（覆盖包含）→ 边对判定
  （共线重叠比较 CCW 内侧方向；垂直真穿越判定）。边界异侧重叠 = 合法共边。
- 共边：对每对边找同支撑线的正长度重叠区间，按 `(方向, fixed)` 分组合并成极大区间。

### 5.3 查询共边不读外环

`GET /api/shared-edges` 只查 `shared_edges` 与 `signoffs` 两张派生表；
有测试用 `set_trace_callback` 断言该请求的全部 SQL 不出现 `parcels`。

### 5.4 原子性

`apply_order` 与批量签收都在单连接 `BEGIN IMMEDIATE` 内完成，任何异常
（含注入的写盘故障）都 `ROLLBACK`。测试 `test_write_failure_keeps_previous_state`
在写库后注入异常，验证外环、单数、共边版本与签收均保持原状。

## 6. 目录结构

```
app/
  main.py        # FastAPI 路由与 /docs
  schemas.py     # pydantic 入参模型（StrictInt、无洞校验）
  service.py     # 整单事务、版本对账、签收、制图供数
  geometry.py    # 环校验 / 内部相交 / 共边区间 / 候选集规划
  db.py          # SQLite schema、测区单例行
  errors.py      # 结构化业务错误
  static/vendor/ # 自托管 Swagger UI 资源(Apache-2.0)
scripts/trial.py # 25 条断言的叙述式试跑
tests/           # 29 个 pytest 用例（几何 + 端到端 + 事务回滚）
Dockerfile
docker-compose.yml
```
