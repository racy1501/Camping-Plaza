# 露营广场 集成审计

依据：《露营广场｜阶段性确定稿 v0.3》
实现：`camping_plaza/game_engine.py`、`camping_plaza/game_api.py`（含前端 frontend/、schema.sql、README.md）
方式：语法检查、模块导入、直接函数调用、临时内存状态测试（3天全流程 fuzz、turn_settled 恢复、故障阻塞、预定链路、MCP 路由函数）。未安装依赖、未连外网、未修改任何文件。

# 审计结论

FOUND_ISSUES

基础项：语法 PASS；导入 PASS；启动入口 `python game_api.py` 存在且可用；多天 fuzz 无异常；turn_settled 恢复不重复结算 PASS；Turn 6 旧异常维修 PASS；决策点补足数量正确 PASS；MCP 操作链（/mcp/state → /mcp/actions → /api/action → /api/turn/advance）可闭环。

# 问题

## 1. 严重程度：中

- 文件与位置：`game_engine.py` → `get_full_state()`， tents 使用 `asdict(t)` 整体导出
- 真实问题：帐篷内部字段 `next_breakdown_turn`（故障概率排期）经 `/api/state`、`/api/state/display` 对外返回。违反设计 2.1「隐藏标签、概率计算等数据留在代码后台，不在每轮重复返回」。NPC 与预定已做脱敏，帐篷漏了。已实测确认字段出现在接口返回中。
- 复现步骤：`eng.get_full_state()["tents"][1].keys()` → 含 `next_breakdown_turn`、`satisfaction_bonus`；启动服务后 `GET /api/state` 同样可见。
- 最小修复方向：`get_full_state()` 中帐篷改为与 NPC 相同的安全字典，仅保留 status / level / capacity / occupied_by（或删除 next_breakdown_turn 一项）。

## 2. 严重程度：中

- 文件与位置：`game_engine.py` `__init__(db_path)`；`schema.sql`；`README.md` 技术栈与对接说明
- 真实问题：`db_path` 参数接收后从未使用，`schema.sql` 定义的 6 张表无任何代码读写，游戏状态全部在内存。服务重启即丢档（day、balance、帐篷等级、好评率、预定全部归零）。README 声明 SQLite 技术栈、合集站按长期经营对接，实际实现与该契约断链。
- 复现步骤：全局搜索 `db_path` / `sqlite3` → 仅构造函数一处赋值，无任何读写；重启进程后状态重置。
- 最小修复方向：二选一——接入 schema.sql 做最简存档/读档（启动时 load、每次 advance_turn 后 save）；或在 README 与构造函数中明确「当前版本无持久化」，删除死代码避免误导对接方。

## 3. 严重程度：低

- 文件与位置：`game_engine.py` → `accept_reservation()` / `reject_reservation()`
- 真实问题：两方法只有 `turn_settled` 检查，缺少「存在 broken 帐篷禁止经营操作」的引擎级保护（improve_service / upgrade_tent / upgrade_facility / manage_greenery 均有）。直接调用 `/api/action` 可在故障未维修时接受/拒绝预定，与已确立的故障优先规则不一致。MCP 路径因操作列表过滤不会触发，仅绕过 API 列表直达接口时可复现。另两方法也无 Turn 阶段限制（Turn 6 可直接调用接受预定）。
- 复现步骤：`tents[1].status="broken"` 且存在 pending 预定时调用 `eng.accept_reservation(2)` → 返回 success（已实测）。
- 最小修复方向：在两方法开头补 broken 检查（与 improve_service 相同文案），按需补 Turn 1-5 阶段检查。

## 4. 严重程度：低

- 文件与位置：`game_api.py` → `do_action()` 的 `manage_greenery` 分支
- 真实问题：返回 `{"result": "..."}`，其余所有操作返回 `{"success": bool, "message": "..."}`。MCP 封装层或前端统一按 `success`/`message` 解析时，绿化操作会被误判为失败或丢失文案（前端 `doAction` 读 `result.message` 已实测拿到 undefined 回退文案）。
- 复现步骤：`do_action(ActionRequest(action="manage_greenery", ...))` → 键仅 `result`。
- 最小修复方向：`manage_greenery()` 返回值包一层 `{"success": True/False, "message": ...}`，或在 do_action 分支转换。

## 5. 严重程度：低

- 文件与位置：`frontend/scripts/app.js`（renderTentsPanel、updateUI）、`frontend/index.html`、`frontend/scripts/map.js` `_getTentColor`
- 真实问题：后端已新增 `reserved` 帐篷状态与 `campsite`（营位费）收入，前端未对齐：① 帐篷面板 statusIcons 与地图配色均无 `reserved` 分支，预定帐篷显示 ❓ 与底色；② 收入面板只有住宿/餐饮/娱乐三格，营位费不展示（设计 §11/§20 要求营位费在结果中体现）。
- 复现步骤：制造一顶 reserved 帐篷后 `GET /api/state` → 前端帐篷卡显示 ❓；任意日间客到达产生营位费 → 面板不更新。
- 最小修复方向：statusIcons 与 _getTentColor 补 `reserved`（🔵），收入面板补一格营位费。

## 6. 严重程度：低

- 文件与位置：`game_api.py` `/api/map`；`frontend/scripts/map.js` positions；`README.md` 对接说明第 5 步
- 真实问题：README 写明围观前端调用 `/api/map` 更新地图，但前端实际只用 `/api/state` + 本地硬编码坐标，从未请求 `/api/map`。且两端键名不一致：API 返回 `hot_spring` / `entertainment_a` / `entertainment_b`，前端用 `hotSpring` / `entertainmentA` / `entertainmentB`。一旦按 README 接线即取不到坐标。
- 复现步骤：对比 `get_map_data()["positions"]` 键名与 map.js `this.positions` 键名。
- 最小修复方向：统一一端键名（建议 API 改驼峰对齐前端），或README 更正为「前端使用 /api/state + 本地坐标」。

## 7. 严重程度：低

- 文件与位置：`game_engine.py` → `_generate_daily_reservation()`
- 真实问题：pending 预定若一直不接受/不拒绝，会永久滞留，且 `_generate_daily_reservation` 因 `reservation is not None` 直接 return，预定系统从此不再产生新预定（已实测跨天仍滞留）。不影响主循环，但预定功能无声停摆。
- 复现步骤：生成预定后不处理，连续 `_new_day()` → reservation 保持，新预定不再生成。
- 最小修复方向：`_new_day()` 中对前一日未处理的 pending 预定按超时自动拒绝（可复用拒绝的抱怨判定），或保留但明确设计为「AI 可无限期搁置」。

## 8. 严重程度：低

- 文件与位置：`game_engine.py` → `reject_reservation()`
- 真实问题：设计 §16.1 已确认「拒绝/无法接受预定时，判定成功则触发抱怨剧情 + 第二天来客率极轻微临时下降，一天后消失」。当前只实现了抱怨剧情，次日来客率下降未实现。
- 复现步骤：`reject_reservation()` 多次调用，次日 `_generate_day_guests` / `_generate_overnight_guests` 无任何来客率修正因子。
- 最小修复方向：增加一个仅次日有效的轻量标记（如 `next_day_guest_penalty`），在生成日间客时 count-1 后清除；或在设计稿中删除该条。
