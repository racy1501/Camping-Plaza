# 露营广场 Camping Plaza

> MCP 经营游戏 · AI负责经营，人类围观

## 项目概述

露营广场是一个运行在 [CEDAR TOY](https://toy.cedarstar.org/) 合集站上的 MCP 游戏。AI 通过 MCP 接口经营露营广场，人类玩家通过围观界面实时观看 AI 的经营过程。

## 架构

```
┌─────────────────────────────────┐
│  CEDAR TOY 合集站               │
│  ├── MCP 封装层（合集站维护）    │
│  └── 人类围观前端               │
└──────────────┬──────────────────┘
               │
        ┌──────▼──────┐
        │  游戏后端     │ ← 本仓库
        │  Python+API  │
        └─────────────┘
```

## 目录结构

```
camping_plaza/
├── game_engine.py      # 游戏核心逻辑（回合推进、NPC、结算）
├── game_api.py         # API接口（MCP + 前端）
├── schema.sql          # 数据库结构
├── requirements.txt    # Python依赖
└── README.md           # 本文件
```

## 游戏玩法

- **类型**：经营游戏，无限模式
- **核心循环**：接待客人 → 获得收入 → 升级设施 → 提升好评率 → 吸引更多客人
- **AI决策**：维修帐篷、购买成长项目、管理绿化（预约由代码自动生成和结算，无需人工或 AI 接受/拒绝）
- **每回合**：3个经营决策点，清洁帐篷不占决策点

## 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动API服务
python game_api.py

# 访问 http://localhost:8000/docs 查看API文档
```

## API接口

> 说明：`/mcp/state` 与 `/mcp/actions` 是普通 FastAPI HTTP 端点，采用适合 MCP 封装层消费的结构；本仓库本身不是原生 MCP 协议服务器。

### 状态查询
- `GET /api/state` — 完整游戏状态
- `GET /api/state/display` — 展示用文本+数据
- `GET /api/map` — 地图位置数据（帐篷、设施、NPC坐标）

### MCP专用
- `GET /mcp/state` — AI 决策需要的精简状态
- `GET /mcp/actions` — 当前可用操作列表

`/mcp/state` 主要字段（按经营、设施、预约/客流、Turn Plan 分组）：

- **经营状态**：`day`、`turn`、`balance`、`average_rating`、`decisions_left`、`food_stock`、`today_income`
- **设施/帐篷**：`tents`（含 status/unlocked/capacity）、`facilities`（含 level）、`greenery`、`hot_spring`、`day_campsite`
- **预约/客流**：`active_guests_count`、`reservations`、`arrival_plan`
- **Turn Plan**：`planning_available`、`plan_submitted`、`plan_target_turn`、`turn_plan`、`next_turn_checkout_tents`

`turn_plan` 说明：

- 只存在于 `/mcp/state`；
- 无已提交计划时为 `null`；
- 有计划时包含：`target_day`、`target_turn`、`free_actions`、`decision_actions`；
- 只暴露动作名和白名单参数摘要，不暴露原始 `pending_turn_plan`。

`/mcp/actions` 说明：

- 只暴露代码判定 `can_purchase_now == true` 的成长项目；
- 成长购买动作为 `purchase_growth_project`，参数为 `project_id`；
- 旧 `upgrade_facility` 不再由 `/mcp/actions` 推荐；
- 旧 `upgrade_facility` 仍作为 `/api/action` 的兼容执行入口保留，但不建议新接入使用。

### 游戏操作
- `POST /api/turn/advance` — 推进回合
- `POST /api/action` — 执行经营操作（维修帐篷、购买成长项目、管理绿化等）

#### 预约机制

- 预约由代码自动生成、接下、错失和结算；
- 不存在 `accept_reservation` / `reject_reservation` 动作；
- 玩家和 AI 无需手动接受或拒绝预约；
- 对外安全 `reservations` 字段为列表；每项包含 `group_size`、`visit_type`、`arrival_day`、`status`，过夜预约额外包含 `tent_id`。

#### `/api/action` 错误语义

- **请求语义错误**：HTTP 400，结构 `{"detail": {"error_code": "...", "message": "..."}}`，当前错误码：`missing_tent_id`、`missing_facility_name`、`missing_package_key`、`invalid_project_id`、`unknown_action`。
- **请求体 schema 校验错误**：HTTP 422，由 FastAPI/Pydantic 返回。
- **合法业务动作被拒绝**：仍可能返回 HTTP 200 + `success: false` + `message`（此时部分失败动作可能已写入状态）。

不要把所有失败都当作 HTTP 400 处理。

### 前端调用行为（doAction）

- 检查 `response.ok`；
- HTTP 错误优先显示 `detail.message`；
- HTTP 非成功响应不刷新状态；
- HTTP 200 + `success: false` 仍刷新状态，因为部分业务失败可能已写入状态。

## 存档

- 使用 `runtime_snapshot` JSON 快照保存完整运行状态，每个 `session_id` 对应一份独立存档。
- 配置 `DATABASE_URL` 且其为 `postgres://` 或 `postgresql://` 时，使用 PostgreSQL；启动时会执行 `CREATE TABLE IF NOT EXISTS`，不会覆盖已有存档。
- 本地未配置 `DATABASE_URL` 时，使用 SQLite，默认路径为 `camping_plaza/camping_plaza.db`（基于 `game_api.py` 所在目录，不依赖启动目录）。
- 服务重启后自动从快照恢复；存档损坏时停止启动，避免覆盖已有存档。
- PostgreSQL 与 SQLite 都支持多个独立 session；Render 不需要新增环境变量。

## Session 存档 API

先调用 `POST /api/session` 创建一份新存档，返回不可预测的 `session_id`。

- 所有读取状态的 `GET /api/*` 与 `GET /mcp/*` 请求都使用 query 参数：`?session_id=...`。
- 所有修改状态的 `POST /api/*` 请求都在 JSON body 中携带：`"session_id": "..."`。
- 缺少 `session_id` 会返回 HTTP 400 和 `missing_session_id`；不存在的存档返回 HTTP 404 和 `session_not_found`，不会回退到共享默认存档。
- 人类网页首次访问会自动创建 session 并写入浏览器 `localStorage`；可用 `/?session_id=...` 打开指定测试档，验证成功后会保存为当前网页存档。

## Render 部署

- Build Command：`pip install -r requirements.txt`
- Start Command：`uvicorn game_api:app --host 0.0.0.0 --port $PORT`
- Render 环境变量：`DATABASE_URL`（Neon PostgreSQL 连接地址）；`PORT` 由 Render 自动提供。
- 本地不配置 `DATABASE_URL` 时继续使用 SQLite。云端正式存档使用 Neon PostgreSQL，不需要 Render Persistent Disk。

## 设计文档

完整游戏规则见 `DESIGN.md`（v0.3）

## 技术栈

- Python 3.10+
- FastAPI
- SQLite

## 合集站对接说明

合集站通过 MCP 封装层调用本项目的 API：
1. 调用 `GET /mcp/state` 获取当前状态
2. 调用 `GET /mcp/actions` 获取可用操作
3. AI 决策后调用 `POST /api/action` 执行操作
4. 调用 `POST /api/turn/advance` 推进回合
5. 围观前端调用 `GET /api/map` 更新地图显示

## License

MIT
