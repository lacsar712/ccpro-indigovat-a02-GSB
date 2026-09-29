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

1. **染缸还原台**：横滑缸位条，每缸显示状态、最近电位、redox sparkline 与「本周兑比合格/无合格单」徽章
2. **还原母液兑比专页**（顶栏入口）：兑比单列表、染缸工新建、主管作废
3. **工坊 chip**：仅作缸位筛选，无独立工坊 CRUD 页
4. **点缸展开**：同页内登记浸染批次、改状态、看近几笔；无平行「染缸表 / 批次表」

**业务规则**：

- 状态改为 `ready`（可染色）时，最新批次 `redoxMv` 须已填且 ≤ -500（见 `vat_rules.py`）。
- **闲置 → 还原中**必须持有合格的还原母液兑比单，判定函数 `assert_can_start_reducing` 与改状态共用，绕过兑比专页直接改状态同样拦截：
  - **自然周规则**：按自然周（周一 00:00 起算），同一染缸最多保留一张**未作废**兑比单；周内重复开单拒绝并指出已有单号。主管作废后方可重开。
  - **五日规则（与自然周并行）**：读取该缸**本自然周最新一张合格、未作废**单，开单日距今超过 5 个自然日即中文拒绝。
  - 兑比单：母液升与清水升均须 > 0，且母液 ≤ 清水的 40%；化验不合格（`passed=false`）或已作废的单不得作为合格依据。
  - 权限：染缸工（普通账号）开单，主管（`is_superuser`）作废；作废留痕（作废人/时间）。

种子：`V-02` 为一口闲置缸且无任何兑比单（演示被拦截）；`V-01` 持有本周新鲜合格单。

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
4. **ReductionMixOrder**：还原母液兑比单 `code`、染缸、`issuedOn`、`motherL`、`waterL`、`passed`、`chemist`、开单人、作废标记/作废人/作废时间

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
