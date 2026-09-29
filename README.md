# IndigoVat-01 · 染缸还原台

FastAPI + PostgreSQL + Jinja2：主界面是横向**缸位条**（Alpine 反应式），不是工坊/染缸/批次三表导航。Session Cookie 登录；规则在 `app/services/vat_rules.py`。

## 技术栈

- FastAPI、SQLAlchemy 2、PostgreSQL
- 启动时 `create_all` + 幂等种子（蓝靛湾一号坊 / 清水江二号坊）
- Session Cookie 认证（Starlette SessionMiddleware）
- Jinja2 + Alpine.js + Pico（叠靛蓝水墨自定义样式）
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4720** |
| Postgres | **6120**（容器内 5432） |

数据库账号：`indigovat` / `indigovat` / 库名 `indigovat`

## 快速启动

```bash
cd IndigoVat/IndigoVat-01
docker compose up --build -d
```

浏览器打开：http://localhost:4720

演示账号（登录页已预填）：

- `admin` / `123456`
- `worker` / `123456`

## 交互（信息架构）

1. **染缸还原台**：横滑缸位条，每缸显示状态、最近电位、redox sparkline 与**本周兑比徽标**
2. **工坊 chip**：仅作缸位筛选，无独立工坊 CRUD 页
3. **点缸展开**：同页内登记浸染批次、改状态、看近几笔；无平行「染缸表 / 批次表」
4. **兑比单专页**（顶栏「兑比单」，`/mix-orders`）：兑比单列表、染缸工开单、主管作废

**业务规则**：

- 状态改为 `ready`（可染色）时，最新批次 `redoxMv` 须已填且 ≤ -500（见 `vat_rules.py`）。
- **闲置缸改 `reducing`（还原中）必须凭还原母液兑比单**，判定在状态变更入口统一执行（见
  `services/vat_rules.py` → `services/mix_orders.py`），绕过兑比专页直接改状态同样被拒。

### 兑比单规则（自然周与五日并行）

兑比单字段：染缸、开单日、母液升、清水升、是否合格、化验人（另有单号、开单人、作废留痕）。

1. **兑比约束**：母液升与清水升都须 > 0，且母液升 ≤ 清水升 × 40%。
2. **自然周（周一为一周起点）**：
   - 同一染缸同一自然周内最多保留一张**未作废单**（合格/不合格均占位）；
     周内重复开单被拒绝，并提示已有单号；需重开须先由主管作废原单。
   - 改还原中只认**本周**最新的合格、未作废单。
3. **5 个自然日新鲜度**：本周合格单开单日距今超过 5 个自然日（开单当天记 0 天）即失效，中文拒绝。
   自然周管「本周有没有单」，五日管「单子新不新鲜」，两条规则并行。
4. **角色分工**：染缸工开单（`worker`），只有主管（`admin`，`is_superuser`）可作废；
   作废单立即失去合格依据效力，永不再作为还原依据。

种子数据包含一口专供本流程的闲置缸 `V-21`（本周无合格单）。

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6120
uvicorn app.main:app --host 0.0.0.0 --port 4720 --reload
```

## 业务模型

1. **Workshop**：`name`、`region`、`notes`（UI 上仅为筛选片）
2. **Vat**：归属工坊、`code`、`dyeType`、`volumeL`、状态 `idle|reducing|ready`
3. **DipLot**：归属染缸、`dippedAt`、`clothMeters`、`redoxMv`（可空）
4. **MixOrder**：归属染缸的还原母液兑比单，`issuedOn`、`motherL`、`waterL`、
   `isQualified`、`chemist`、开单人，以及 `voided/voided_by/voidedAt` 作废留痕；
   单号由 id 生成（`M0001` 起）

## 目录结构

```
IndigoVat-01/
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  requirements.txt
  app/
    main.py
    db.py
    models.py
    schemas.py
    auth.py
    seed.py
    routers/
    services/vat_rules.py
    templates/   # base / bay / login
```
