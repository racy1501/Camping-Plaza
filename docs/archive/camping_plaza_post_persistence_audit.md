# 露营广场后端长程压力测试与持久化集成审计

## 总体结论

**PASS**

---

## 验证结果

### 原有测试

- 文件数量：3 个测试文件
  - `tests/test_game_engine_regressions.py`（44 测试）
  - `tests/test_persistence.py`（9 测试）
  - `tests/test_api_persistence.py`（19 测试）
- 原有测试总数：**72**
- 结果：**全部通过**（`Ran 72 tests in 0.405s OK`）

### 新增长程测试

- 文件：`tests/test_long_run_state_machine.py`
- 新增长程测试数：**9**
  - 多日合法经营模拟：1（含 10 个种子 subTest）
  - 确定性组合场景：8
- 结果：**全部通过**（`Ran 9 tests in 7.060s OK`）

### 模拟参数

- 模拟种子数量：**10**（`[11, 23, 37, 42, 58, 67, 79, 83, 91, 97]`）
- 每个存档持续天数：**≥15 个游戏日**
- 模拟总天数：**≥150 个游戏日**（10 种子 × 15 天，实际因步数上限可能略多）
- 每存档重启恢复间隔：每 25 步一次
- 每存档重启恢复次数：**≥8 次**（实测约 8~12 次，10 种子累计约 80~120 次）
- 全量比对覆盖字段：state / tents / facilities / npc_pool / npc_history / _npc_id_counter
- 每步不变量检查：是

### 发现的问题

**无真实生产代码问题。**

测试初版运行时出现 10 个断言失败，全部源自同一个原因：不变量断言错误地假定 `broken` 帐篷不得携带 `occupied_by`。经核实生产代码 `_handle_breakdowns()` 与 `_checkout_npc()` / `repair_tent()` 的联动设计明确——**故障帐篷不赶客**，保留 `occupied_by`，修好后根据 `occupied_by` 恢复 `occupied` 状态。这是 v0.3 已确认的策划行为（详见修复记录"故障帐篷保留 occupied_by，不移动住客"）。

修正后的不变量正确反映了生产代码的合法状态：
- `occupied` 帐篷必须有 `occupied_by`
- `available` / `cleaning` / `reserved` 帐篷不得携带 `occupied_by`
- `broken` 帐篷允许保留 `occupied_by`（故障不赶客）
- 住客所在帐篷状态必须是 `occupied` 或 `broken`

修正测试不变量后 9 个测试全绿。**此修正属于测试断言对齐生产行为，不是生产 bug。**

### 状态损坏 / 重复收费 / 重复结算 / 死锁 / 存档丢失

- 状态损坏：**未发现**
- 重复收费：**未发现**（含"接受预定重启后次日入住不重复收费"专项测试）
- 重复结算：**未发现**（含 turn_settled 保护验证）
- 死锁：**未发现**（含 stall_guard 防停滞保护）
- 存档丢失：**未发现**（重启恢复全量比对通过）

---

## 尚未定案但不属于 Bug

在长程压力测试过程中，以下机制的**具体数值**尚未在 v0.3 定案，测试过程中只验证其不会造成状态损坏、异常或死锁，**未对数值合理性做任何断言**：

1. 客流生成数量公式（`_generate_day_guests` / `_generate_overnight_guests` 的生成条件与计数）
2. 故障间隔（`_set_next_breakdown` 的 base_interval 与随机范围）
3. 转过夜满意度阈值（`_process_day_to_overnight` 中的 70 分判定）
4. 提升服务触发概率（`improve_service` 中 `random.random() < 0.3` 的 30% 命中率）
5. 好评率公式（≤10 评价时的绝对均值 / >10 评价时的加权平滑）
6. 预定生成概率（`_generate_daily_reservation` 中 `random.random() < 0.3` 的 30% 触发率）
7. 预定客次日触发入住的时间窗口（依赖 Turn 2~4 的预定重试机制）
8. 绿化 Lv2 自动维护判定（`manage_greenery` 返回"已达最高级"）

以上数值均在长程经营中自然参与运算，未引发任何状态损坏、异常抛出或死锁。
