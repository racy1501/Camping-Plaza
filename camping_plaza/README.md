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
- **AI决策**：维修帐篷、升级设施、接受/拒绝预定、管理绿化
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

### 状态查询
- `GET /api/state` — 完整游戏状态
- `GET /api/state/display` — 展示用文本+数据
- `GET /api/map` — 地图位置数据（帐篷、设施、NPC坐标）

### MCP专用
- `GET /mcp/state` — AI决策需要的精简状态
- `GET /mcp/actions` — 当前可用操作列表

### 游戏操作
- `POST /api/turn/advance` — 推进回合
- `POST /api/action` — 执行操作（维修/升级/接受预定等）

## 存档

- 使用 SQLite 单行 JSON 快照（`runtime_snapshot` 表）保存完整运行状态。
- 正式数据库路径：`camping_plaza/camping_plaza.db`（基于 `game_api.py` 所在目录，不依赖启动目录）。
- 服务重启后自动从快照恢复；存档损坏或缺失时安全回退到新游戏，不影响启动。
- 现有六张规范化表暂时保留，但不作为运行存档来源。
- 单存档，无账号、多存档位或云同步。

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
