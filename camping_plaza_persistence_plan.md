# 露营广场 SQLite 持久化方案（只读审计结论）

审计时间：2026-07-27
审计范围：game_engine.py / game_api.py / schema.sql / README.md / tests/test_game_engine_regressions.py
约束：只读审计，不修改任何文件；仅用标准库 sqlite3；单存档；无 ORM；优先最小、稳定、完整快照。

---

## 1. 现有六张表是否足以保存最新运行状态

**不足。** schema.sql 是 v0.1 版本结构，之后多轮修复新增的字段均未入库：

| 表 | 缺失字段 | 影响 |
|---|---|---|
| game_state | reserved_tent_day、greenery_processed_today、today_events、day_to_overnight_cache、turn_settled、_npc_id_counter | 预定日期丢失导致预定客永不入住；绿化标记丢失导致重复衰减/重复打理；转过夜缓存丢失导致 Turn 5 无展示；turn_settled 丢失导致故障阻塞回合重复结算；NPC ID 计数器丢失导致恢复后 ID 冲突 |
| tents | status 枚举缺 'reserved'（注释未列出，列本身可存） | 预定帐篷恢复后变成 available，会被普通客占用 |
| npcs | is_reserved、paid | 预定客身份丢失，重复收费或预定无法结清 |
| facilities | 齐全（dining/entertainment/greenery 全部字段已有列） | 无 |
| npc_history | 基本齐全 | 无 |
| event_log | 与状态恢复无关（追加型日志） | 无 |

reservation_data（TEXT/JSON）列本身可容纳含隐藏标签的完整预定，但 game_state 行缺上述标量字段，整体仍不完整。

## 2. 方案比较

**方案 A：沿用六张规范化表 + 补迁移字段**
- 需要为 5 张表写双向行映射（约 200+ 行读写代码）
- 需要 ALTER TABLE 迁移逻辑兼容旧库
- 恢复时要按外键/ID 重建对象图，任何一张表不一致都会产生半恢复状态

**方案 B：新增单行 runtime_snapshot 表，整体 JSON 快照**
- 引擎内部本来就是 dataclass 对象图，`asdict` 一次序列化
- 表结构：`runtime_snapshot(id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER, payload TEXT, updated_at TEXT)`，单行覆盖写
- event_log 表可保留不动（属日志，不参与恢复），其余规范化表不再承担运行时状态

| 维度 | A 规范化表 | B JSON 快照 |
|---|---|---|
| 改动量 | 大（5 表映射 + 迁移） | 小（序列化/反序列化各一） |
| 旧库兼容 | 需 ALTER 迁移，逐个字段兜底 | 无快照即视为新档，天然兼容 |
| 状态完整性 | 依赖映射覆盖度，易漏字段 | 100%（对象图原样落盘） |
| 恢复失败风险 | 部分表损坏产生半恢复脏状态 | 解析失败整体回退新档，无中间态 |
| 新增字段成本 | 每次改表 + 改映射 + 改迁移 | 零成本（dataclass 加字段即自动入库） |
| 测试难度 | 需逐表逐字段断言 | 一次恢复后整体比对即可 |

**结论：选 B。** 本项目是单实例、单存档、状态体积小（NPC 池峰值几十个对象）的 MCP 小游戏，快照方案在完整性、可维护性、恢复安全性上全面占优；规范化表的查询优势在本项目没有使用场景。event_log 若要保留日志能力可继续沿用现有表，与快照互不干扰。

## 3. 启动流程

1. `db_path` 文件不存在 → 初始化新游戏，建快照表，立即写入一次初始快照。
2. 文件存在且快照行可解析、schema_version 匹配、payload 关键键齐全（state/tents/npcs/facilities/npc_id_counter）→ 反序列化恢复，跳过 `_init_game` 的默认初始化。
3. 文件存在但出现以下任一情况 → **回退新档**，绝不上抛异常：
   - 文件不是合法 SQLite（损坏）
   - 快照表不存在或快照行为空（空库/旧库无快照）
   - JSON 解析失败或关键键缺失
   - 回退前将损坏文件重命名为 `camping_plaza.db.corrupt-<时间戳>` 留证，再建新档。
4. 加载逻辑包裹在 try/except 内，任何异常路径最终都落到可用引擎实例，**服务启动不受存档影响**。

## 4. 保存时点逐项检查

| 操作 | 是否改状态 | 说明 |
|---|---|---|
| advance_turn | ✅ | 改 day/turn/余额/收入/NPC/故障/缓存/turn_settled/decisions_left，且**多个早退分支也改状态**（broken 阻塞补足决策点、结算后新故障置 turn_settled） |
| repair_tent | ✅ | 帐篷状态、next_breakdown_turn、decisions_left |
| clean_tents | ✅ | 帐篷状态（cleaning→available/reserved） |
| accept_reservation | ✅ | 余额、收入、reservation、reserved_tent_id/day |
| reject_reservation | ✅ | reservation 清空、today_events 可能追加抱怨 |
| upgrade_tent | ✅ | 余额、等级、satisfaction_bonus、故障排期 |
| upgrade_facility | ✅ | 余额、设施字段 |
| improve_service | ✅ | decisions_left、NPC 满意度 |
| manage_greenery | ✅ | 余额、绿化字段、greenery_processed_today |

**注意：advance_turn 不能只按"成功返回"保存**，broken 阻塞与 turn_settled 分支同样产生了状态变更（补足决策点、标记已结算），漏存会导致恢复后死锁参数丢失。

## 5. 保存触发方：引擎方法自存 vs API 层统一保存

**判定：序列化逻辑放引擎（`save()` / 构造时自动 `_load()`），保存触发放 game_api.py 统一执行。**

理由：
- 引擎公共方法分散自存容易遗漏分支（advance_turn 有 3 处 return），且测试会引入大量磁盘 IO；
- 所有写入口都经过 API 层（`/api/action`、`/api/turn/advance`），MCP 与前端只读接口不产生状态变更；
- API 层规则简单可枚举：**每个非只读端点调用完成后无条件 `engine.save()`**（不按业务 success 过滤，见第 4 节 advance_turn 说明）；
- 引擎保持纯逻辑，`save()` 本身是纯方法，可独立单测。

## 6. 持久化字段清单

**必须持久化（完整内部状态，含对外隐藏字段）：**
- GameState 全部：day、turn、balance、reputation_rate、total_reviews、total_rating_sum、today_income、today_events、decisions_left、reservation（完整 dict，含 group_size/economic_level/spending_habit/temperament/tent_id）、reserved_tent_id、reserved_tent_day、greenery_processed_today、day_to_overnight_cache、turn_settled
- tents 六顶全字段：id、capacity、level、status（含 reserved）、occupied_by、next_breakdown_turn、satisfaction_bonus
- npc_pool 全字段：id、group_size、visit_type、arrival_turn、location、total_satisfaction、has_left、review_left、review_rating、economic_level、spending_habit、temperament、visit_count、last_visit_day、is_reserved、paid
- npc_history 完整列表
- facilities 三个设施全部字段
- `_npc_id_counter`（防恢复后 ID 冲突）

**不持久化（派生/常量）：**
- advance_turn 返回的 result、get_full_state 脱敏视图、_get_tents_summary/_get_npcs_summary 输出——均由内部状态实时派生
- TENT_PRICES、CAPACITY_MAP、升级费用表等类常量
- 随机数生成器内部状态

## 7. 测试设计（标准库 unittest）

- 全部使用 `tempfile.TemporaryDirectory` 下的临时 db_path，**断言测试目录与 cwd 均不产生正式 camping_plaza.db**。
- 用例清单：
  1. 新档首次创建自动生成快照文件；
  2. 修改状态（推进回合、制造 NPC/预定/故障/清洁/升级）→ save → 同路径新建引擎实例 → 第 6 节全部字段逐一比对一致（含隐藏标签、turn_settled、day_to_overnight_cache、_npc_id_counter 后续生成 ID 不冲突）；
  3. 连续两次 save 后快照表仍只有一行（覆盖写，无脏数据追加）；
  4. 写入垃圾字节的损坏 db → 新实例回退新档且旧文件被备份重命名；
  5. 空文件 / 只有表没有快照行的库 → 回退新档；
  6. payload JSON 截断损坏 → 回退新档；
  7. 恢复后 advance_turn 行为与未中断时一致（抽样验证回合推进不异常）。
- 随机行为沿用现有 mock.patch 手法控制。

## 8. 下一轮施工估算

| 项 | 内容 |
|---|---|
| 修改文件 | camping_plaza/game_engine.py（+约 90~120 行：建表、save、_load、损坏回退）；camping_plaza/game_api.py（+约 8~12 行：写端点统一调 save） |
| 新增文件 | tests/test_persistence.py（+约 130~160 行，7 个用例）；可选：无 |
| schema.sql | **建议追加** runtime_snapshot 表定义（约 6 行），与引擎内建表 SQL 保持一致；旧六张表不删不动 |
| README | **建议追加** 3~5 行"存档"说明（自动生成 camping_plaza.db、损坏自动回退新档） |
| 风险等级 | 低-中。风险集中在 `__init__` 加载分支：必须保证 db 损坏/缺失时行为与现状完全一致，且现有 44 个回归测试（均用默认 db_path 构造引擎）不受影响——施工时引擎默认参数保持 `db_path="camping_plaza.db"`，但加载失败静默回退，测试隔离依赖各测试自行清理或走临时路径 |
| 前置确认 | 现有回归测试直接用 `CampingPlazaEngine()` 默认构造，若默认路径落盘会互相污染；施工需让测试基类统一传临时 db_path（属测试文件改动，需用户确认范围） |

## 9. 明确不做

- 不设计账号/多存档位/云同步；
- 不引入 ORM、不装第三方依赖；
- 不沿用六张规范化表做运行时状态恢复（event_log 日志能力保留可选）；
- 不修改任何游戏机制、数值、前端；
- 不因 schema.sql 已存在而强行套结构。
