"""
露营广场 - 游戏核心引擎
类型：MCP 经营游戏，AI负责经营，人类围观
设计版本：v0.3
"""

import os
import random
import json
import sqlite3
from typing import Optional
from dataclasses import dataclass, field, asdict


# =============================================================================
# 数据结构
# =============================================================================

@dataclass
class Tent:
    """帐篷"""
    id: int
    capacity: int
    is_unlocked: bool = False
    level: int = 0
    status: str = "available"  # available, occupied, cleaning, broken, reserved
    occupied_by: Optional[int] = None  # NPC组ID
    next_breakdown_turn: int = 0
    satisfaction_bonus: float = 0.0

    CAPACITY_MAP = {1: 2, 2: 2, 3: 3, 4: 3, 5: 4, 6: 5}


@dataclass
class NPCGroup:
    """NPC客人组"""
    id: int
    group_size: int
    visit_type: str  # "day" | "overnight"
    arrival_turn: int = 0
    location: str = "gate"  # gate, campsite, tent_1-6, dining, entertainment, leaving
    total_satisfaction: int = 60
    has_left: bool = False
    review_left: bool = False
    review_rating: int = 0

    # 隐藏标签
    economic_level: int = 0  # 0-2: 低/中/高
    spending_habit: int = 0  # 0-2: 吝啬/普通/大方
    temperament: int = 0  # 0-2: 温和/普通/暴躁

    # 回访记录
    visit_count: int = 1
    last_visit_day: int = 0
    last_dining_day: int = 0
    checkout_turn: Optional[int] = None

    # 预定标记
    is_reserved: bool = False  # 是否是预定客
    paid: bool = False  # 是否已付款


@dataclass
class Facility:
    """设施（餐饮区、娱乐区、绿化）"""
    name: str
    level: int = 0
    # 餐饮
    dining_spend_probability: float = 0.6
    dining_income_multiplier: float = 1.0
    dining_satisfaction: float = 5.0
    # 娱乐
    entertainment_satisfaction: float = 8.0
    entertainment_income_multiplier: float = 1.0
    # 绿化
    greenery_satisfaction: float = 3.0
    greenery_decay_rate: float = 1.0


@dataclass
class GameState:
    """游戏全局状态"""
    day: int = 1
    turn: int = 1  # 1-5 营业回合, 6 日终管理
    balance: int = 1000
    reputation_rate: float = 60.0
    total_reviews: int = 0
    total_rating_sum: int = 0
    pending_reviews: list = field(default_factory=list)
    pending_turn_plan: Optional[dict] = None

    today_income: dict = field(default_factory=lambda: {
        "accommodation": 0,
        "campsite": 0,
        "dining": 0,
        "entertainment": 0
    })
    today_events: list = field(default_factory=list)
    decisions_left: int = 3
    day_campsite_groups_served: int = 0
    food_stock: int = 0
    last_food_preorder_day: int = 0

    # 预定
    reservation: Optional[dict] = None  # 待处理的预定请求
    reserved_tent_id: Optional[int] = None  # 已确认并分配帐篷的预定
    reserved_tent_day: Optional[int] = None  # 预定入住日期

    # 绿化每日标记
    greenery_processed_today: bool = False

    # 修复 #4：转过夜结果缓存
    day_to_overnight_cache: list = field(default_factory=list)

    # 修复：营业回合结算后产生故障，标记本回合结算已完成
    turn_settled: bool = False

    today_arrival_plan_day: int = 0
    today_arrival_plan: list = field(default_factory=list)


# =============================================================================
# 游戏引擎
# =============================================================================

class CampingPlazaEngine:
    """露营广场游戏引擎"""

    DAY_CAMPSITE_CAPACITY = 10
    TENT_PRICES = {1: 80, 2: 120, 3: 120, 4: 180, 5: 180, 6: 300}
    CAMPSITE_FEE = 20
    DINING_BASE_PRICE = 30
    ENTERTAINMENT_BASE_PRICE = 40
    DINING_PLANNED_ACTION_PROBABILITIES = {0: 0.55, 1: 0.70, 2: 0.85}
    PAID_ENTERTAINMENT_PLANNED_ACTION_PROBABILITIES = {0: 0.30, 1: 0.50, 2: 0.70}
    FREE_ENTERTAINMENT_PLANNED_ACTION_PROBABILITY = 0.50
    DINING_SET_MENUS = {
        "basic": {
            "display_name": "基础套餐",
            "price_per_person": 30,
            "satisfaction_gain": 2,
            "required_dining_level": 0,
        },
        "standard": {
            "display_name": "中档套餐",
            "price_per_person": 45,
            "satisfaction_gain": 4,
            "required_dining_level": 1,
        },
        "premium": {
            "display_name": "高档套餐",
            "price_per_person": 65,
            "satisfaction_gain": 6,
            "required_dining_level": 2,
        },
    }
    DINING_SET_MENU_ORDER = ("basic", "standard", "premium")
    ECONOMIC_LEVEL_TO_DINING_SET_MENU = {0: "basic", 1: "standard", 2: "premium"}
    FOOD_PACKAGES = {
        "small": {"name": "小包", "portions": 4, "price": 80},
        "medium": {"name": "中包", "portions": 8, "price": 150},
        "large": {"name": "大包", "portions": 14, "price": 250},
    }
    OPENING_FOOD_GIFT_PACKAGE = "medium"

    TENT_UPGRADE_COST = [0, 500, 1200, 2500]
    FACILITY_UPGRADE_COST = [0, 400, 1000, 2000]
    GREENERY_UPGRADE_COST = [0, 300, 800]
    TURN_PLAN_ACTIONS = {
        "clean_tents": {
            "kind": "free",
            "required": (),
            "optional": ("tent_ids",),
        },
        "repair_tent": {
            "kind": "decision",
            "required": ("tent_id",),
            "optional": (),
        },
        "improve_service": {
            "kind": "decision",
            "required": (),
            "optional": (),
        },
        "buy_food_package": {
            "kind": "decision",
            "required": ("package_key",),
            "optional": (),
        },
    }

    # 快照版本号，结构变更时递增
    SNAPSHOT_VERSION = 1

    def __init__(self, db_path: str = "camping_plaza.db"):
        self.db_path = db_path
        self.state = GameState()
        self.tents: dict[int, Tent] = {}
        self.npc_pool: list[NPCGroup] = []
        self.npc_history: list[dict] = []
        self.facilities: dict[str, Facility] = {}
        self._npc_id_counter = 0
        self._init_game()
        # 持久化：仅在明确新游戏时创建快照表；已有数据库先读后写
        if not os.path.exists(self.db_path):
            self._ensure_snapshot_table()
            self._apply_opening_food_gift()
            self.save_state()
        else:
            load_result = self.load_state()
            if load_result == "no_snapshot":
                self._apply_opening_food_gift()
                self.save_state()
            elif load_result == "load_error":
                raise RuntimeError(
                    "存档加载失败，游戏已停止启动，以避免覆盖现有存档。"
                )

        if self.state.turn == 1 and self._ensure_today_arrival_plan():
            self.save_state()

    def _current_turn_plan_target(self) -> tuple[int, int]:
        return self.state.day, self.state.turn

    def _build_arrival_plan_entry(
        self,
        npc: NPCGroup,
        arrival_turn: int,
        source: str,
        *,
        tent_id: Optional[int] = None,
    ) -> dict:
        return {
            "npc_id": npc.id,
            "group_size": npc.group_size,
            "visit_type": npc.visit_type,
            "economic_level": npc.economic_level,
            "spending_habit": npc.spending_habit,
            "temperament": npc.temperament,
            "total_satisfaction": npc.total_satisfaction,
            "arrival_turn": arrival_turn,
            "planned_day": self.state.day,
            "source": source,
            "arrival_status": "pending",
            "planned_actions": [],
            "is_reserved": npc.is_reserved,
            "paid": npc.paid,
            "tent_id": tent_id,
        }

    def _schedule_planned_actions(
        self, arrival_turn: int, planned_actions: list[dict], latest_turn: int = 5
    ) -> list[dict]:
        if arrival_turn > latest_turn:
            raise ValueError("arrival_turn cannot be later than latest_turn")
        if not planned_actions:
            return planned_actions
        available_turns = list(range(arrival_turn, latest_turn + 1))
        if len(planned_actions) > len(available_turns):
            raise ValueError("planned action count exceeds available turns")

        scheduled_actions = list(planned_actions)
        random.shuffle(scheduled_actions)
        scheduled_turns = sorted(
            random.sample(available_turns, len(scheduled_actions))
        )
        for action, planned_turn in zip(scheduled_actions, scheduled_turns):
            action["planned_turn"] = planned_turn
        planned_actions.sort(key=lambda action: action["planned_turn"])
        return planned_actions

    def _build_dining_planned_action(self, entry: dict) -> Optional[dict]:
        probability = self.DINING_PLANNED_ACTION_PROBABILITIES.get(
            entry["spending_habit"], self.DINING_PLANNED_ACTION_PROBABILITIES[1]
        )
        if random.random() >= probability:
            return None
        return {
            "action": "dining",
            "preferred_menu": self._get_target_dining_set_menu_key(
                entry["economic_level"]
            ),
            "status": "pending",
        }

    def _build_paid_entertainment_planned_action(self, entry: dict) -> Optional[dict]:
        probability = self.PAID_ENTERTAINMENT_PLANNED_ACTION_PROBABILITIES.get(
            entry["spending_habit"],
            self.PAID_ENTERTAINMENT_PLANNED_ACTION_PROBABILITIES[1],
        )
        if random.random() >= probability:
            return None
        return {
            "action": "paid_entertainment",
            "status": "pending",
        }

    def _build_free_entertainment_planned_action(self) -> Optional[dict]:
        if random.random() >= self.FREE_ENTERTAINMENT_PLANNED_ACTION_PROBABILITY:
            return None
        return {
            "action": "free_entertainment",
            "status": "pending",
        }

    def _can_plan_paid_entertainment(self) -> bool:
        return self.facilities["entertainment"].level >= 1

    def _append_planned_actions(self, entry: dict):
        entry["planned_actions"].clear()
        charged_actions = []
        dining_action = self._build_dining_planned_action(entry)
        if dining_action is not None:
            charged_actions.append(dining_action)
        if self._can_plan_paid_entertainment():
            paid_entertainment_action = self._build_paid_entertainment_planned_action(entry)
            if paid_entertainment_action is not None:
                charged_actions.append(paid_entertainment_action)
        free_entertainment_action = self._build_free_entertainment_planned_action()

        entry["planned_actions"].extend(charged_actions)
        available_turn_count = 5 - entry["arrival_turn"] + 1
        if (
            free_entertainment_action is not None
            and len(entry["planned_actions"]) < available_turn_count
        ):
            entry["planned_actions"].append(free_entertainment_action)
        self._schedule_planned_actions(
            entry["arrival_turn"], entry["planned_actions"]
        )

    def _find_arrival_plan_entry(
        self,
        *,
        npc_id: Optional[int] = None,
        source: Optional[str] = None,
        tent_id: Optional[int] = None,
    ) -> Optional[dict]:
        for entry in self.state.today_arrival_plan:
            if entry.get("planned_day") != self.state.day:
                continue
            if npc_id is not None and entry.get("npc_id") != npc_id:
                continue
            if source is not None and entry.get("source") != source:
                continue
            if tent_id is not None and entry.get("tent_id") != tent_id:
                continue
            return entry
        return None

    def _calculate_day_guest_demand(self) -> int:
        count = random.randint(2, 5)
        if self.state.reputation_rate > 70:
            count += 1
        elif self.state.reputation_rate < 50:
            count = max(1, count - 1)
        return max(1, count)

    def _calculate_overnight_guest_demand(self) -> int:
        count = 0
        for _tent in self._get_unlocked_tents():
            if random.random() < 0.6:
                count += 1
        return count

    def _calculate_daily_visitor_demand(self) -> dict:
        return {
            "day_guest_count": self._calculate_day_guest_demand(),
            "overnight_guest_count": self._calculate_overnight_guest_demand(),
        }

    def _create_day_guest(self) -> NPCGroup:
        npc = NPCGroup(
            id=self._next_npc_id(),
            group_size=random.randint(1, 3),
            visit_type="day",
        )
        self._assign_hidden_tags(npc)
        return npc

    def _create_overnight_guest(self) -> NPCGroup:
        max_capacity = max((tent.capacity for tent in self._get_unlocked_tents()), default=1)
        npc = NPCGroup(
            id=self._next_npc_id(),
            group_size=random.randint(1, max_capacity),
            visit_type="overnight",
        )
        self._assign_hidden_tags(npc)
        return npc

    def _ensure_today_arrival_plan(self) -> bool:
        if self.state.today_arrival_plan_day == self.state.day:
            return False

        demand = self._calculate_daily_visitor_demand()
        planned_entries = []

        natural_guests = [
            self._create_day_guest()
            for _ in range(demand["day_guest_count"])
        ]
        natural_guests.extend(
            self._create_overnight_guest()
            for _ in range(demand["overnight_guest_count"])
        )

        arrival_turns = (2, 3, 4)
        for index, guest in enumerate(natural_guests):
            arrival_turn = arrival_turns[index % len(arrival_turns)]
            source = "natural_day" if guest.visit_type == "day" else "natural_overnight"
            entry = self._build_arrival_plan_entry(guest, arrival_turn, source)
            self._append_planned_actions(entry)
            planned_entries.append(entry)

        if (
            self.state.reservation is not None
            and self.state.reserved_tent_id is not None
            and self.state.reserved_tent_day == self.state.day
        ):
            reservation_npc_id = self.state.reservation.setdefault("npc_id", self._next_npc_id())
            reserved_guest = NPCGroup(
                id=reservation_npc_id,
                group_size=self.state.reservation["group_size"],
                visit_type="overnight",
                total_satisfaction=60,
                is_reserved=True,
                paid=True,
            )
            reserved_guest.economic_level = self.state.reservation.get("economic_level", 1)
            reserved_guest.spending_habit = self.state.reservation.get("spending_habit", 1)
            reserved_guest.temperament = self.state.reservation.get("temperament", 1)
            entry = self._build_arrival_plan_entry(
                reserved_guest,
                2,
                "reservation",
                tent_id=self.state.reserved_tent_id,
            )
            self._append_planned_actions(entry)
            planned_entries.append(entry)

        self.state.today_arrival_plan = planned_entries
        self.state.today_arrival_plan_day = self.state.day
        return True
    def _validate_turn_plan_action(
        self, action_data: dict, expected_kind: str
    ) -> tuple[bool, Optional[dict], str]:
        if not isinstance(action_data, dict):
            return False, None, "invalid action payload"

        action_name = action_data.get("action")
        config = self.TURN_PLAN_ACTIONS.get(action_name)
        if not config or config["kind"] != expected_kind:
            return False, None, "unsupported action"

        allowed_keys = {"action", *config["required"], *config["optional"]}
        if any(key not in allowed_keys for key in action_data):
            return False, None, "invalid action payload"
        if any(key not in action_data for key in config["required"]):
            return False, None, "invalid action payload"

        normalized = {"action": action_name}
        for key in config["required"] + config["optional"]:
            if key in action_data:
                normalized[key] = action_data[key]

        if action_name == "clean_tents":
            tent_ids = normalized.get("tent_ids")
            if tent_ids is not None:
                if (
                    not isinstance(tent_ids, list)
                    or any(not isinstance(tid, int) for tid in tent_ids)
                    or len(set(tent_ids)) != len(tent_ids)
                ):
                    return False, None, "invalid action payload"
        elif action_name == "repair_tent":
            if not isinstance(normalized.get("tent_id"), int):
                return False, None, "invalid action payload"
        elif action_name == "buy_food_package":
            if not isinstance(normalized.get("package_key"), str):
                return False, None, "invalid action payload"

        return True, normalized, ""

    def _drop_expired_turn_plan(self, result: Optional[dict] = None) -> bool:
        plan = self.state.pending_turn_plan
        if not plan:
            return False
        if (plan.get("target_day"), plan.get("target_turn")) == self._current_turn_plan_target():
            return False
        self.state.pending_turn_plan = None
        if result is not None:
            result["events"].append("stale turn plan discarded")
        return True

    def _require_turn_plan_for_advance(self, result: dict) -> bool:
        self._drop_expired_turn_plan(result)
        if self.state.turn not in (2, 3, 4, 5):
            return True
        if self.state.pending_turn_plan is not None:
            return True
        result["events"].append("submit turn plan first")
        return False

    def submit_turn_plan(self, free_actions: Optional[list], actions: Optional[list]) -> dict:
        free_actions = [] if free_actions is None else free_actions
        actions = [] if actions is None else actions

        self._drop_expired_turn_plan()

        if self.state.turn not in (2, 3, 4, 5):
            return {"success": False, "message": "planning unavailable"}
        if self.state.pending_turn_plan is not None:
            return {"success": False, "message": "turn plan already submitted"}
        if not isinstance(free_actions, list) or not isinstance(actions, list):
            return {"success": False, "message": "invalid turn plan"}
        if len(actions) > 3:
            return {"success": False, "message": "too many actions"}

        normalized_free_actions = []
        for action_data in free_actions:
            ok, normalized, message = self._validate_turn_plan_action(action_data, "free")
            if not ok:
                return {"success": False, "message": message}
            normalized_free_actions.append(normalized)

        normalized_actions = []
        for action_data in actions:
            ok, normalized, message = self._validate_turn_plan_action(action_data, "decision")
            if not ok:
                return {"success": False, "message": message}
            normalized_actions.append(normalized)

        target_day, target_turn = self._current_turn_plan_target()
        self.state.pending_turn_plan = {
            "target_day": target_day,
            "target_turn": target_turn,
            "free_actions": normalized_free_actions,
            "actions": normalized_actions,
        }
        self.state.decisions_left = 0
        return {
            "success": True,
            "target_day": target_day,
            "target_turn": target_turn,
            "free_actions_count": len(normalized_free_actions),
            "actions_count": len(normalized_actions),
        }

    def _run_turn_plan_action(self, action_data: dict) -> dict:
        action_name = action_data["action"]
        if action_name == "clean_tents":
            result = self.clean_tents(action_data.get("tent_ids"))
        elif action_name == "repair_tent":
            result = self.repair_tent(action_data["tent_id"], consume_decision=False)
        elif action_name == "buy_food_package":
            result = self._buy_food_package(action_data["package_key"])
        else:
            result = self.improve_service(consume_decision=False)
        return {
            "action": action_name,
            "success": bool(result.get("success")),
            "message": result.get("message", ""),
        }

    def _buy_food_package(self, package_key: str) -> dict:
        package = self.FOOD_PACKAGES.get(package_key)
        if package is None:
            return {"success": False, "message": "invalid food package"}

        price = package["price"]
        if self.state.balance < price:
            return {"success": False, "message": f"余额不足，需要{price}金币"}

        portions = package["portions"]
        name = package["name"]
        self.state.balance -= price
        self.state.food_stock += portions
        return {
            "success": True,
            "message": f"已购买{name}（{portions}份），花费{price}金币",
            "package_key": package_key,
            "portions": portions,
            "price": price,
        }

    def buy_food_package(self, package_key: str) -> dict:
        if self.state.turn_settled:
            return {"success": False, "message": "本回合已经结算，请进入下一个回合"}
        if self.state.turn != 6:
            return {"success": False, "message": "食材预购只能在日终管理阶段（Turn 6）进行"}
        if self.state.last_food_preorder_day == self.state.day:
            return {"success": False, "message": "今天已经完成过食材预购"}

        result = self._buy_food_package(package_key)
        if result.get("success"):
            self.state.last_food_preorder_day = self.state.day
        return result

    def _execute_pending_turn_plan(self, result: dict):
        plan = self.state.pending_turn_plan
        result["plan_execution"] = {"free_actions": [], "actions": []}
        if not plan:
            return
        if (plan.get("target_day"), plan.get("target_turn")) != self._current_turn_plan_target():
            result["events"].append("stale turn plan discarded")
            self.state.pending_turn_plan = None
            return

        for action_data in plan.get("free_actions", []):
            result["plan_execution"]["free_actions"].append(
                self._run_turn_plan_action(action_data)
            )
        for action_data in plan.get("actions", []):
            result["plan_execution"]["actions"].append(
                self._run_turn_plan_action(action_data)
            )
        self.state.pending_turn_plan = None

    def _apply_opening_food_gift(self):
        package = self.FOOD_PACKAGES[self.OPENING_FOOD_GIFT_PACKAGE]
        self.state.food_stock = package["portions"]
        self.state.today_events.append(self._build_opening_food_gift_event())

    def _build_opening_food_gift_event(self) -> str:
        package = self.FOOD_PACKAGES[self.OPENING_FOOD_GIFT_PACKAGE]
        package_label = f'{package["name"]}（{package["portions"]}份）'
        return (
            "开业物资已送达：为了帮助你顺利营业，"
            f"特别赠送食材{package_label}。祝你经营顺利！"
        )

    # -------------------------------------------------------------------------
    # SQLite JSON 快照持久化（单行覆盖，runtime_snapshot 为唯一权威存档）
    # -------------------------------------------------------------------------

    def _ensure_snapshot_table(self):
        """创建或确认 runtime_snapshot 表存在。失败不抛异常，不影响服务启动"""
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS runtime_snapshot (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        snapshot_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                    )
                """)
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def save_state(self) -> bool:
        """将当前完整运行状态以 JSON 快照单行覆盖写入数据库。

        多次保存只更新 id=1 同一行；保存失败返回 False，不影响游戏操作本身。
        """
        try:
            payload = {
                "snapshot_version": self.SNAPSHOT_VERSION,
                "state": asdict(self.state),
                "tents": {str(tid): asdict(t) for tid, t in self.tents.items()},
                "facilities": {name: asdict(f) for name, f in self.facilities.items()},
                "npc_pool": [asdict(n) for n in self.npc_pool],
                "npc_history": list(self.npc_history),
                "npc_id_counter": self._npc_id_counter,
            }
            data = json.dumps(payload, ensure_ascii=False)
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("""
                    INSERT INTO runtime_snapshot (id, snapshot_json, updated_at)
                    VALUES (1, ?, datetime('now', 'localtime'))
                    ON CONFLICT(id) DO UPDATE SET
                        snapshot_json = excluded.snapshot_json,
                        updated_at = excluded.updated_at
                """, (data,))
                conn.commit()
            finally:
                conn.close()
            return True
        except Exception:
            return False

    def load_state(self) -> str:
        """从数据库加载 id=1 快照并原子式恢复完整运行状态。

        数据库不存在、表为空、JSON 损坏、版本不匹配或任何嵌套结构无法恢复时
        返回 False。失败过程不修改当前引擎实例的任何字段，确保 __init__ 后续
        save_state() 只写入完整的新游戏状态。
        """
        try:
            if not os.path.exists(self.db_path):
                return "no_snapshot"
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT snapshot_json FROM runtime_snapshot WHERE id = 1"
                ).fetchone()
            finally:
                conn.close()
            if not row:
                return "no_snapshot"

            payload = json.loads(row[0])
            if not isinstance(payload, dict):
                return "load_error"

            required = {"snapshot_version", "state", "tents", "facilities",
                        "npc_pool", "npc_history", "npc_id_counter"}
            if not required.issubset(payload.keys()):
                return "load_error"

            if int(payload["snapshot_version"]) != self.SNAPSHOT_VERSION:
                return "load_error"

            # 原子恢复：所有对象先在局部构造，全部成功后再一次性赋值给实例字段
            state_fields = {f for f in GameState.__dataclass_fields__}
            restored_state = GameState()
            for key, value in payload["state"].items():
                if key in state_fields:
                    setattr(restored_state, key, value)

            restored_tents = {}
            for tid, tdata in payload["tents"].items():
                tent_id = int(tid)
                normalized_tdata = dict(tdata)
                if "is_unlocked" not in normalized_tdata:
                    normalized_tdata["is_unlocked"] = self._default_tent_unlocked_state(tent_id)
                restored_tents[tent_id] = Tent(**normalized_tdata)
            restored_facilities = {name: Facility(**fdata)
                                   for name, fdata in payload["facilities"].items()}
            restored_npc_pool = []
            for ndata in payload["npc_pool"]:
                normalized_ndata = dict(ndata)
                normalized_ndata.setdefault("last_dining_day", 0)
                restored_npc_pool.append(NPCGroup(**normalized_ndata))
            restored_npc_history = list(payload["npc_history"])
            restored_npc_id_counter = int(payload["npc_id_counter"])

            # 全部构造成功才提交到实例字段
            self.state = restored_state
            self.tents = restored_tents
            self.facilities = restored_facilities
            self.npc_pool = restored_npc_pool
            self.npc_history = restored_npc_history
            self._npc_id_counter = restored_npc_id_counter
            return "loaded"
        except Exception:
            return "load_error"

    def _init_game(self):
        """初始化游戏"""
        for i in range(1, 7):
            self.tents[i] = Tent(
                id=i,
                capacity=Tent.CAPACITY_MAP[i],
                is_unlocked=self._default_tent_unlocked_state(i),
            )
            self._set_next_breakdown(self.tents[i])

        self.facilities["dining"] = Facility(name="餐饮区")
        self.facilities["entertainment"] = Facility(name="娱乐区", level=0)
        self.facilities["greenery"] = Facility(
            name="绿化", level=1, greenery_decay_rate=0.5
        )

    # -------------------------------------------------------------------------
    # NPC ID 生成
    # -------------------------------------------------------------------------

    def _next_npc_id(self) -> int:
        """生成全局唯一NPC ID"""
        self._npc_id_counter += 1
        return self._npc_id_counter

    # -------------------------------------------------------------------------
    # 修复 #3 辅助方法：判断帐篷是否为今日预定帐篷
    # -------------------------------------------------------------------------

    def _is_today_reserved_tent(self, tent_id: int) -> bool:
        """判断帐篷是否为今日预定帐篷"""
        return (self.state.reserved_tent_id == tent_id
                and self.state.reserved_tent_day == self.state.day)

    def _default_tent_unlocked_state(self, tent_id: int) -> bool:
        return tent_id == 1

    def _is_tent_unlocked(self, tent: Tent) -> bool:
        return tent.is_unlocked

    def _get_unlocked_tents(self) -> list[Tent]:
        return [
            self.tents[tid]
            for tid in sorted(self.tents.keys())
            if self._is_tent_unlocked(self.tents[tid])
        ]

    def _get_broken_tents(self) -> list[Tent]:
        return [tent for tent in self._get_unlocked_tents() if tent.status == "broken"]

    def _get_available_unlocked_tents(
        self,
        group_size: Optional[int] = None,
        *,
        exclude_today_reserved: bool = True,
    ) -> list[Tent]:
        tents = []
        for tent in self._get_unlocked_tents():
            if tent.status != "available":
                continue
            if group_size is not None and tent.capacity < group_size:
                continue
            if exclude_today_reserved and self._is_today_reserved_tent(tent.id):
                continue
            tents.append(tent)
        return tents

    def _absolute_turn(self) -> int:
        """计算绝对回合数，避免跨天重置导致故障无法正常触发"""
        return (self.state.day - 1) * 6 + self.state.turn

    # -------------------------------------------------------------------------
    # 回合推进
    # -------------------------------------------------------------------------

    def advance_turn(self) -> dict:
        """推进一个回合，返回结算后的真实状态"""
        result = {
            "events": [],
            "next_actions": []
        }

        # 修复：将已有 today_events 加入本次事件并清空，避免重复展示
        result["events"].extend(self.state.today_events)
        self.state.today_events.clear()

        if self.state.turn <= 5:
            if not self._require_turn_plan_for_advance(result):
                result["day"] = self.state.day
                result["turn"] = self.state.turn
                result["income"] = dict(self.state.today_income)
                result["balance"] = self.state.balance
                result["reputation"] = self.state.reputation_rate
                result["tents"] = self._get_tents_summary()
                result["npcs"] = self._get_npcs_summary()
                return result

            self._process_business_turn(result)
            self._process_dining(result)
            self._process_entertainment(result)
            if self.state.turn == 5:
                self._process_turn5_day_guest_departures(result)
            self._handle_breakdowns(result)
            if self.state.turn == 5:
                self.state.food_stock = 0
            self.state.decisions_left = 3

            # 推进到下一回合
            if self.state.turn < 6:
                self.state.turn += 1
            else:
                self._new_day(result)
        else:
            self._process_day_end(result)
            self.state.turn_settled = False
            self._new_day(result)

        # 在所有结算完成后，重新获取最新状态
        result["day"] = self.state.day
        result["turn"] = self.state.turn
        result["income"] = dict(self.state.today_income)
        result["balance"] = self.state.balance
        result["reputation"] = self.state.reputation_rate
        result["tents"] = self._get_tents_summary()
        result["npcs"] = self._get_npcs_summary()

        return result

    def _process_business_turn(self, result: dict):
        """处理营业回合"""
        turn = self.state.turn

        if turn == 1:
            self._process_checkout_partial(result)

        elif turn == 2:
            self._process_checkout_all(result)
            self._execute_pending_turn_plan(result)
            self._assign_reserved_tent_for_today()
            self._process_reservations(result)  # 修复 #1：预定客尝试入住
            self._process_checkin(result)

        elif turn == 3:
            self._execute_pending_turn_plan(result)
            # 修复 #1：Turn 3继续尝试预定客入住
            self._process_reservations(result)
            self._process_checkin(result)

        elif turn == 4:
            self._execute_pending_turn_plan(result)
            self._process_day_to_overnight(result)  # 修复 #4：先处理Turn 4开始前已在营地的日间客
            self._process_reservations(result)  # 修复 #1：预定客Turn 4继续重试入住
            self._process_checkin(result)

        elif turn == 5:
            self._execute_pending_turn_plan(result)
            # 修复 #4：展示Turn 4的转过夜缓存
            self._flush_day_to_overnight_cache(result)

        # 清理已离开的NPC
        self._cleanup_left_npcs()

    def _process_checkout_partial(self, result: dict):
        """Turn 1: 部分过夜客退房"""
        overnight_npcs = self._get_active_overnight_tent_npcs()

        for npc in overnight_npcs:
            if self._ensure_checkout_turn(npc) == 1:
                self._checkout_npc(npc, result)

    def _process_checkout_all(self, result: dict):
        """Turn 2: 剩余过夜客全部退房"""
        overnight_npcs = self._get_active_overnight_tent_npcs()

        for npc in overnight_npcs:
            self._checkout_npc(npc, result)

    def _checkout_npc(self, npc: NPCGroup, result: dict):
        """NPC退房"""
        tent_id = int(npc.location.split("_")[1])
        tent = self.tents[tent_id]
        was_broken = tent.status == "broken"
        # 修复：故障帐篷退房后保持 broken，必须经过 repair_tent() 才能恢复使用
        if was_broken:
            tent.status = "broken"
        else:
            tent.status = "cleaning"
        tent.occupied_by = None
        npc.location = "leaving"
        npc.has_left = True

        satisfaction_change = self.facilities["greenery"].greenery_satisfaction
        npc.total_satisfaction = min(100, npc.total_satisfaction + satisfaction_change)

        result["events"].append(f"帐篷{tent_id}号客人退房")
        self._try_leave_review(npc, result)

    # -------------------------------------------------------------------------
    # 入住与日间客
    # -------------------------------------------------------------------------

    def _process_planned_arrivals(self, result: dict):
        if self.state.today_arrival_plan_day != self.state.day:
            self._ensure_today_arrival_plan()

        for entry in self.state.today_arrival_plan:
            if entry.get("planned_day") != self.state.day:
                continue
            if entry.get("arrival_status") != "pending":
                continue
            if entry.get("arrival_turn") != self.state.turn:
                continue
            if entry.get("source") == "reservation":
                continue

            if entry.get("source") == "natural_day":
                if self.get_day_campsite_remaining() <= 0:
                    entry["arrival_status"] = "turned_away_full"
                    result["events"].append("日间营位已经客满，一组刚到的客人只能遗憾离开。")
                    continue

                guest = NPCGroup(
                    id=entry["npc_id"],
                    group_size=entry["group_size"],
                    visit_type="day",
                    total_satisfaction=entry["total_satisfaction"],
                )
                guest.economic_level = entry["economic_level"]
                guest.spending_habit = entry["spending_habit"]
                guest.temperament = entry["temperament"]
                guest.location = "campsite"
                guest.arrival_turn = self.state.turn
                self.npc_pool.append(guest)
                self.state.day_campsite_groups_served += 1
                self.state.balance += self.CAMPSITE_FEE
                self.state.today_income["campsite"] += self.CAMPSITE_FEE
                entry["arrival_status"] = "arrived"
                result["events"].append(
                    f"一组{guest.group_size}人日间游客到达（营位费+{self.CAMPSITE_FEE}）"
                )
            elif entry.get("source") == "natural_overnight":
                guest = NPCGroup(
                    id=entry["npc_id"],
                    group_size=entry["group_size"],
                    visit_type="overnight",
                    total_satisfaction=entry["total_satisfaction"],
                )
                guest.economic_level = entry["economic_level"]
                guest.spending_habit = entry["spending_habit"]
                guest.temperament = entry["temperament"]
                tent_id = self._find_available_tent(guest.group_size)
                if tent_id is not None:
                    self._checkin_npc(guest, tent_id, result, charge=True)
                    entry["arrival_status"] = "arrived"
                else:
                    entry["arrival_status"] = "turned_away_full"
                    result["events"].append("过夜客到达时帐篷已经客满，只能遗憾离开。")
    def _process_checkin(self, result: dict):
        """处理入住"""
        if self.state.turn not in [2, 3, 4]:
            return
        self._process_planned_arrivals(result)
        return

        # 分别生成两类客源
        day_guests = self._generate_day_guests()
        overnight_guests = self._generate_overnight_guests()

        # 过夜客入住（修复 #3：普通客不能占用今日预定帐篷）
        for guest in overnight_guests:
            tent_id = self._find_available_tent(guest.group_size)
            if tent_id:
                self._checkin_npc(guest, tent_id, result, charge=True)

        # 日间客入住
        for guest in day_guests:
            guest.location = "dining" if random.random() < 0.5 else "entertainment"
            self.npc_pool.append(guest)
            self.state.day_campsite_groups_served += 1
            self.state.balance += self.CAMPSITE_FEE
            self.state.today_income["campsite"] += self.CAMPSITE_FEE
            result["events"].append(
                f"一组{guest.group_size}人日间游客到达（营位费+{self.CAMPSITE_FEE}）"
            )

    def _checkin_npc(self, npc: NPCGroup, tent_id: int, result: dict, charge: bool = True):
        """NPC入住帐篷"""
        tent = self.tents[tent_id]
        tent.status = "occupied"
        tent.occupied_by = npc.id
        npc.location = f"tent_{tent_id}"
        npc.arrival_turn = self.state.turn
        self._ensure_checkout_turn(npc)

        if charge:
            income = self.TENT_PRICES[tent_id]
            self.state.balance += income
            self.state.today_income["accommodation"] += income

        satisfaction_gain = 10 + tent.level * 3
        npc.total_satisfaction = min(100, npc.total_satisfaction + satisfaction_gain)

        if npc not in self.npc_pool:
            self.npc_pool.append(npc)
        result["events"].append(f"一组{npc.group_size}人入住{tent_id}号帐篷")

    # -------------------------------------------------------------------------
    # 餐饮与娱乐
    # -------------------------------------------------------------------------

    def _process_dining(self, result: dict):
        """处理餐饮消费"""
        facility = self.facilities["dining"]
        for entry in self.state.today_arrival_plan:
            if entry.get("planned_day") != self.state.day:
                continue
            for action in entry.get("planned_actions", []):
                if action.get("action") != "dining":
                    continue
                if action.get("status") != "pending":
                    continue
                if action.get("planned_turn") != self.state.turn:
                    continue
                if entry.get("arrival_status") != "arrived":
                    action["status"] = "skipped"
                    action["result"] = "not_arrived"
                    continue

                npc = self._find_npc(entry["npc_id"])
                if npc is None:
                    action["status"] = "skipped"
                    action["result"] = "missing_npc"
                    continue
                if npc.has_left:
                    action["status"] = "skipped"
                    action["result"] = "npc_left"
                    continue
                if self._has_consumed_dining_today(npc):
                    action["status"] = "skipped"
                    action["result"] = "already_dined"
                    continue

                menu_key = self._get_dining_set_menu_key(
                    entry["economic_level"], facility.level
                )
                menu = self.DINING_SET_MENUS[menu_key]
                required_portions = npc.group_size
                current_stock = self.state.food_stock
                action["actual_menu"] = menu_key

                if current_stock < required_portions:
                    action["status"] = "failed"
                    action["result"] = "insufficient_food"
                    result["events"].append(
                        f"{npc.group_size}人客人想在餐饮区用餐，但食材不足：需要{required_portions}份，当前只有{current_stock}份"
                    )
                    continue

                spend = menu["price_per_person"] * npc.group_size
                if spend <= 0:
                    action["status"] = "skipped"
                    action["result"] = "invalid_spend"
                    continue

                npc.location = "dining"
                self.state.food_stock -= required_portions
                self.state.balance += spend
                self.state.today_income["dining"] += spend
                npc.total_satisfaction = min(
                    100, npc.total_satisfaction + menu["satisfaction_gain"]
                )
                self._mark_dining_consumed(npc)
                action["status"] = "completed"
                action["result"] = "success"
                action["charged_amount"] = spend
                action["food_portions_used"] = required_portions
                action["satisfaction_gain"] = menu["satisfaction_gain"]
                result["events"].append(
                    f"客组{npc.id}购买{menu['display_name']}，"
                    f"{npc.group_size}人用餐，收入+{spend}，"
                    f"消耗食材{required_portions}份，"
                    f"整组满意度+{menu['satisfaction_gain']}"
                )
        return

    def _process_entertainment(self, result: dict):
        """处理娱乐消费"""
        facility = self.facilities["entertainment"]
        for npc in self.npc_pool:
            if npc.has_left:
                continue
            if npc.location == "entertainment":
                probability = self._calc_spend_probability(
                    0.6, npc.spending_habit,
                    low_multiplier=0.7, high_multiplier=1.3
                )
                if random.random() < probability:
                    spend = self._calc_spend_amount(
                        self.ENTERTAINMENT_BASE_PRICE,
                        npc.economic_level,
                        facility.entertainment_income_multiplier
                    )
                    self.state.balance += spend
                    self.state.today_income["entertainment"] += spend

    def _calc_spend_probability(
        self, base_probability: float, spending_habit: int,
        low_multiplier: float = 0.6, high_multiplier: float = 1.5
    ) -> float:
        """根据消费习惯计算消费概率"""
        if spending_habit == 0:
            multiplier = low_multiplier
        elif spending_habit == 2:
            multiplier = high_multiplier
        else:
            multiplier = 1.0
        return max(0.0, min(1.0, base_probability * multiplier))

    def _calc_spend_amount(self, base_amount: int, economic_level: int, multiplier: float) -> int:
        """根据经济水平计算消费金额"""
        if economic_level == 0:
            level_multiplier = 0.8
        elif economic_level == 2:
            level_multiplier = 1.2
        else:
            level_multiplier = 1.0
        return int(base_amount * level_multiplier * multiplier)

    # -------------------------------------------------------------------------
    # 预定系统（修复 #1 #2 #3）
    # -------------------------------------------------------------------------

    def _assign_reserved_tent_for_today(self):
        """新一天开始时，为确认的预定分配帐篷"""
        if (self.state.reserved_tent_id is not None
                and self.state.reserved_tent_day == self.state.day):
            tent = self.tents[self.state.reserved_tent_id]
            if not self._is_tent_unlocked(tent):
                return
            if tent.status == "available":
                tent.status = "reserved"
            elif tent.status == "cleaning":
                pass  # 等清洁完自动变 reserved

    def _process_reservations(self, result: dict):
        """预定客到达入住。修复 #1：只有真正入住后才清除预定"""
        if not (self.state.reserved_tent_id
                and self.state.reserved_tent_day == self.state.day):
            return  # 没有今日预定

        tent_id = self.state.reserved_tent_id
        tent = self.tents[tent_id]
        if not self._is_tent_unlocked(tent):
            return

        plan_entry = self._find_arrival_plan_entry(source="reservation", tent_id=tent_id)


        # 只有帐篷可入住时才处理
        if tent.status in ["available", "reserved"]:
            # 检查预定客是否已经在池中（避免重复创建）
            existing_reserved_npc = None
            for npc in self.npc_pool:
                if npc.is_reserved and npc.location == f"tent_{tent_id}":
                    existing_reserved_npc = npc
                    break

            if existing_reserved_npc:
                # 预定客已入住，清除预定状态
                if plan_entry is not None:
                    plan_entry["arrival_status"] = "arrived"
                self.state.reservation = None
                self.state.reserved_tent_id = None
                self.state.reserved_tent_day = None
            else:
                # 创建预定客NPC
                npc_id = (
                    plan_entry["npc_id"]
                    if plan_entry is not None and plan_entry.get("npc_id") is not None
                    else self.state.reservation.setdefault("npc_id", self._next_npc_id())
                )
                guest = NPCGroup(
                    id=npc_id,
                    group_size=self.state.reservation["group_size"],
                    visit_type="overnight",
                    is_reserved=True,
                    paid=True
                )
                # 修复：恢复预定时保存的三个隐藏标签
                guest.economic_level = self.state.reservation.get("economic_level", 1)
                guest.spending_habit = self.state.reservation.get("spending_habit", 1)
                guest.temperament = self.state.reservation.get("temperament", 1)
                self._checkin_npc(guest, tent_id, result, charge=False)
                result["events"].append(f"预定客人到达，入住{tent_id}号帐篷")
                if plan_entry is not None:
                    plan_entry["arrival_status"] = "arrived"
                # 入住成功后才清除预定状态
                self.state.reservation = None
                self.state.reserved_tent_id = None
                self.state.reserved_tent_day = None
        # 修复 #1：如果帐篷不可入住（cleaning/broken），保留预定信息，等下次重试

    def accept_reservation(self, group_size: int) -> dict:
        """接受预定"""
        # 修复：已结算回合不得再次执行经营操作
        if self.state.turn_settled:
            return {"success": False, "message": "本回合已经结算，请进入下一回合"}
        # 修复：存在故障帐篷时禁止经营操作
        if self._get_broken_tents():
            return {"success": False, "message": "存在故障帐篷，必须先完成维修"}
        # 修复：预定操作仅限营业回合
        if self.state.turn < 1 or self.state.turn > 5:
            return {"success": False, "message": "预定操作只能在营业回合（Turn 1-5）进行"}
        # 修复 #3：没有待处理请求时返回失败
        if self.state.reservation is None:
            return {"success": False, "message": "当前没有待处理的预定请求"}

        # 修复 #2：如果已有未完成入住的预定，不接受新预定
        if self.state.reserved_tent_id is not None:
            return {"success": False, "message": "已有预定的帐篷尚未完成入住"}

        # 修复 #3：使用当前 reservation 中保存的资料，不重新随机生成
        reserved_group_size = self.state.reservation["group_size"]
        economic_level = self.state.reservation.get("economic_level", 1)
        suitable_tents = [
            tent for tent in self._get_unlocked_tents()
            if tent.capacity >= reserved_group_size
        ]
        if not suitable_tents:
            # 策划确认：无法接受预定与主动拒绝统一进行抱怨判定
            self._record_reservation_rejection_event()
            return {"success": False, "message": "没有容量合适的帐篷可预留"}

        tent_id = min(suitable_tents, key=lambda tent: (tent.capacity, tent.id)).id

        payment = self.TENT_PRICES[tent_id]
        self.state.balance += payment
        self.state.today_income["accommodation"] += payment

        # 修复 #3：保留 reservation 到预定客真正入住，只补充 tent_id 和 economic_level
        self.state.reservation["tent_id"] = tent_id
        self.state.reservation["economic_level"] = economic_level
        self.state.reserved_tent_id = tent_id
        self.state.reserved_tent_day = self.state.day + 1

        return {
            "success": True,
            "message": f"预定成功，{tent_id}号帐篷已预留给明天{reserved_group_size}人",
            "payment": payment
        }

    def reject_reservation(self) -> dict:
        """拒绝预定"""
        # 修复：已结算回合不得再次执行经营操作
        if self.state.turn_settled:
            return {"success": False, "message": "本回合已经结算，请进入下一回合"}
        # 修复：存在故障帐篷时禁止经营操作
        if self._get_broken_tents():
            return {"success": False, "message": "存在故障帐篷，必须先完成维修"}
        # 修复：预定操作仅限营业回合
        if self.state.turn < 1 or self.state.turn > 5:
            return {"success": False, "message": "预定操作只能在营业回合（Turn 1-5）进行"}
        # 修复：没有待处理请求或已接受预定时返回失败，不进行随机抱怨判定
        if self.state.reservation is None:
            return {"success": False, "message": "当前没有待处理的预定请求"}
        # 修复：已接受并分配帐篷的预定不能拒绝
        if self.state.reserved_tent_id is not None:
            return {"success": False, "message": "预定已接受，无法拒绝"}
        self.state.reservation = None  # 清空待处理请求
        self._record_reservation_rejection_event()
        return {"success": True, "message": "已拒绝预定"}

    def _record_reservation_rejection_event(self):
        """记录无法接受或拒绝预定导致的客人抱怨事件"""
        if random.random() < 0.3:
            self.state.today_events.append("被拒绝的客人发了条不太满意的帖子")

    # -------------------------------------------------------------------------
    # 日间客转过夜（修复 #3 #4）
    # -------------------------------------------------------------------------

    def _leave_day_guest(self, npc: NPCGroup):
        """日间游客离场，统一写入转过夜缓存并触发评价"""
        npc.has_left = True
        npc.location = "leaving"
        temp_result = {"events": []}
        self._try_leave_review(npc, temp_result)
        for event in temp_result["events"]:
            self.state.day_to_overnight_cache.append(event)

    def _process_turn5_day_guest_departures(self, result: dict):
        """Turn 5营业结算完成后，统一让仍在场的日间客离场。"""
        departing_guests = [
            n for n in self.npc_pool
            if n.visit_type == "day" and not n.has_left
        ]
        if not departing_guests:
            return

        for guest in departing_guests:
            self._leave_day_guest(guest)
        self._cleanup_left_npcs()

    def _process_day_to_overnight(self, result: dict):
        """Turn 4: 日间游客转过夜。修复 #4：结果写入缓存，不立即展示"""
        day_guests = [n for n in self.npc_pool
                     if n.visit_type == "day" and not n.has_left]

        for guest in day_guests:
            if guest.total_satisfaction > 70:
                # 修复 #3：不能占用今日预定帐篷
                tent_id = self._find_available_tent(guest.group_size)
                if tent_id:
                    tent = self.tents[tent_id]
                    tent.status = "occupied"
                    tent.occupied_by = guest.id
                    guest.location = f"tent_{tent_id}"
                    guest.visit_type = "overnight"
                    self._ensure_checkout_turn(guest)
                    income = self.TENT_PRICES[tent_id]
                    self.state.balance += income
                    self.state.today_income["accommodation"] += income
                    # 修复 #4：写入缓存而不是直接写入result
                    self.state.day_to_overnight_cache.append(
                        f"日间游客转为过夜，入住{tent_id}号帐篷（住宿费+{income}）"
                    )
                else:
                    # 想留宿但没有空帐篷，记录遗憾事件后离场
                    if random.random() < 0.4:
                        self.state.day_to_overnight_cache.append(
                            "有日间游客想留下但没空帐篷，不太开心"
                        )
                    self._leave_day_guest(guest)
            else:
                # 不想留宿，直接离场
                self._leave_day_guest(guest)

    def _flush_day_to_overnight_cache(self, result: dict):
        """Turn 5: 展示Turn 4的转过夜缓存。修复 #4"""
        for event in self.state.day_to_overnight_cache:
            result["events"].append(event)
        self.state.day_to_overnight_cache.clear()

    # -------------------------------------------------------------------------
    # 帐篷故障
    # -------------------------------------------------------------------------

    def _handle_breakdowns(self, result: dict):
        """处理帐篷故障"""
        current_turn = self._absolute_turn()
        for tent_id, tent in self.tents.items():
            if not self._is_tent_unlocked(tent):
                continue
            if (tent.status in ["occupied", "available", "reserved"]
                    and current_turn >= tent.next_breakdown_turn
                    and tent.next_breakdown_turn > 0):
                tent.status = "broken"
                # 修复：保留 occupied_by，不移动住客
                result["events"].append(f"⚠️ {tent_id}号帐篷出现故障，需要维修")
                result["next_actions"].append(f"repair_tent_{tent_id}")

    def clean_tents(self, tent_ids: Optional[list[int]] = None) -> dict:
        """AI主动清洁帐篷。不消耗决策点，支持批量清洁。

        Args:
            tent_ids: 要清洁的帐篷ID列表。为None时清洁所有待清洁帐篷。

        Returns:
            {"success": bool, "message": str, "cleaned_tent_ids": list[int]}
        """
        if self.state.turn_settled:
            return {
                "success": False,
                "message": "本回合已经结算，请进入下一回合",
                "cleaned_tent_ids": []
            }
        if self._get_broken_tents():
            return {
                "success": False,
                "message": "存在故障帐篷，必须先完成维修",
                "cleaned_tent_ids": []
            }

        if tent_ids is None:
            target_ids = [
                tid for tid, t in self.tents.items()
                if t.status == "cleaning" and self._is_tent_unlocked(t)
            ]
        else:
            target_ids = [tid for tid in tent_ids
                         if tid in self.tents
                         and self._is_tent_unlocked(self.tents[tid])
                         and self.tents[tid].status == "cleaning"]

        if not target_ids:
            return {
                "success": False,
                "message": "没有待清洁的帐篷",
                "cleaned_tent_ids": []
            }

        cleaned = []
        for tid in target_ids:
            tent = self.tents[tid]
            if self._is_today_reserved_tent(tid):
                tent.status = "reserved"
            else:
                tent.status = "available"
            cleaned.append(tid)

        return {
            "success": True,
            "message": f"已清洁{cleaned}号帐篷",
            "cleaned_tent_ids": cleaned
        }

    # -------------------------------------------------------------------------
    # NPC清理
    # -------------------------------------------------------------------------

    def _cleanup_left_npcs(self):
        """清理所有已离开的NPC"""
        # 修复：离场NPC写入轻量历史
        for npc in self.npc_pool:
            if npc.has_left:
                self.npc_history.append({
                    "id": npc.id,
                    "group_size": npc.group_size,
                    "economic_level": npc.economic_level,
                    "spending_habit": npc.spending_habit,
                    "temperament": npc.temperament,
                    "visit_count": npc.visit_count,
                    "last_visit_day": self.state.day
                })
        self.npc_pool = [n for n in self.npc_pool if not n.has_left]

    # -------------------------------------------------------------------------
    # 日终管理（修复 #5：绿化重复衰减）
    # -------------------------------------------------------------------------

    def _process_day_end(self, result: dict):
        """日终管理阶段"""
        result["events"].append("=== 日终管理阶段 ===")
        result["phase"] = "management"
        result["next_actions"] = [
            "upgrade_tent", "upgrade_facility", "manage_greenery", "next_day"
        ]

    def manage_greenery(self, action: str) -> str:
        """绿化管理"""
        # 修复：已结算回合不得再次执行经营操作
        if self.state.turn_settled:
            return "本回合已经结算，请进入下一回合"
        # 修复：阶段保护，绿化管理仅限日终管理阶段
        if self.state.turn != 6:
            return "绿化管理只能在日终管理阶段（Turn 6）进行"
        # 修复：引擎内部故障保护
        if self._get_broken_tents():
            return "存在故障帐篷，必须先完成维修"
        if self.state.greenery_processed_today:
            return "今天已经处理过绿化了"

        facility = self.facilities["greenery"]
        self.state.greenery_processed_today = True

        if facility.level < 2:
            if action == "maintain":
                cost = 50 * max(1, facility.level)
                self.state.balance -= cost
                facility.greenery_satisfaction = min(10, facility.greenery_satisfaction + 1)
                return f"绿化已打理，花费{cost}金币"
            else:
                facility.greenery_satisfaction = max(
                    0, facility.greenery_satisfaction - facility.greenery_decay_rate
                )
                return f"绿化未打理，环境满意度-{facility.greenery_decay_rate}"
        return "绿化已达最高级（Lv.2），自动维护"

    # -------------------------------------------------------------------------
    # 经营操作
    # -------------------------------------------------------------------------

    def repair_tent(self, tent_id: int, *, consume_decision: bool = True) -> dict:
        """维修帐篷"""
        # 修复：先确认目标帐篷存在且确实为 broken
        tent = self.tents.get(tent_id)
        if not tent or not self._is_tent_unlocked(tent) or tent.status != "broken":
            return {"success": False, "message": "帐篷无需维修"}

        if consume_decision and self.state.decisions_left <= 0:
            return {"success": False, "message": "今日决策点已用完"}

        # 修复：根据住客/预定状态恢复对应状态
        if tent.occupied_by:
            tent.status = "occupied"
        elif self._is_today_reserved_tent(tent_id):
            tent.status = "reserved"
        else:
            tent.status = "available"
        self._set_next_breakdown(tent)
        if consume_decision:
            self.state.decisions_left -= 1
        return {"success": True, "message": f"{tent_id}号帐篷已修好"}

    def upgrade_tent(self, tent_id: int) -> dict:
        """升级帐篷"""
        # 修复：已结算回合不得再次执行经营操作
        if self.state.turn_settled:
            return {"success": False, "message": "本回合已经结算，请进入下一回合"}
        # 修复：阶段保护，升级仅限日终管理阶段
        if self.state.turn != 6:
            return {"success": False, "message": "升级帐篷只能在日终管理阶段（Turn 6）进行"}
        # 修复：引擎内部故障保护
        if self._get_broken_tents():
            return {"success": False, "message": "存在故障帐篷，必须先完成维修"}
        tent = self.tents.get(tent_id)
        if not tent or not self._is_tent_unlocked(tent) or tent.level >= 3:
            return {"success": False, "message": "无法升级"}

        cost = self.TENT_UPGRADE_COST[tent.level + 1]
        if self.state.balance < cost:
            return {"success": False, "message": f"余额不足，需要{cost}金币"}

        self.state.balance -= cost
        tent.level += 1
        tent.satisfaction_bonus = tent.level * 3
        self._set_next_breakdown(tent)
        return {"success": True, "message": f"{tent_id}号帐篷升级到Lv.{tent.level}"}

    def upgrade_facility(self, facility_name: str) -> dict:
        """升级设施"""
        # 修复：已结算回合不得再次执行经营操作
        if self.state.turn_settled:
            return {"success": False, "message": "本回合已经结算，请进入下一回合"}
        # 修复：阶段保护，升级仅限日终管理阶段
        if self.state.turn != 6:
            return {"success": False, "message": "升级设施只能在日终管理阶段（Turn 6）进行"}
        # 修复：引擎内部故障保护
        if self._get_broken_tents():
            return {"success": False, "message": "存在故障帐篷，必须先完成维修"}
        facility = self.facilities.get(facility_name)
        if not facility:
            return {"success": False, "message": "设施不存在"}

        # 修复：绿化最高只能升级到Lv2，餐饮/娱乐维持Lv3
        max_level = 2 if facility_name == "greenery" else 3
        if facility.level >= max_level:
            return {"success": False, "message": "无法升级"}

        # 修复：绿化升级使用 GREENERY_UPGRADE_COST
        if facility_name == "greenery":
            cost = self.GREENERY_UPGRADE_COST[facility.level + 1]
        else:
            cost = self.FACILITY_UPGRADE_COST[facility.level + 1]
        if self.state.balance < cost:
            return {"success": False, "message": f"余额不足，需要{cost}金币"}

        self.state.balance -= cost
        facility.level += 1

        if facility_name == "dining":
            facility.dining_spend_probability = min(0.9, facility.dining_spend_probability + 0.1)
            facility.dining_income_multiplier += 0.2
            facility.dining_satisfaction += 2
        elif facility_name == "entertainment":
            facility.entertainment_satisfaction += 3
            facility.entertainment_income_multiplier += 0.2
        elif facility_name == "greenery":
            facility.greenery_satisfaction += 2
            if facility.level >= 2:
                facility.greenery_decay_rate = 0

        return {"success": True, "message": f"{facility_name}升级到Lv.{facility.level}"}

    def improve_service(self, *, consume_decision: bool = True) -> dict:
        """提升服务"""
        # 修复：已结算回合不得再次执行经营操作
        if self.state.turn_settled:
            return {"success": False, "message": "本回合已经结算，请进入下一回合"}
        # 修复：阶段保护，提升服务仅限营业回合
        if self.state.turn > 5:
            return {"success": False, "message": "提升服务只能在营业回合（Turn 1-5）进行"}
        # 修复：故障优先，存在故障帐篷时必须先把决策点留给维修
        if self._get_broken_tents():
            return {"success": False, "message": "存在故障帐篷，必须先完成维修"}
        if consume_decision and self.state.decisions_left <= 0:
            return {"success": False, "message": "今日决策点已用完"}

        if consume_decision:
            self.state.decisions_left -= 1
        affected = []
        for npc in self.npc_pool:
            if not npc.has_left and random.random() < 0.3:
                npc.total_satisfaction = min(100, npc.total_satisfaction + 5)
                affected.append(npc.id)

        return {"success": True, "message": f"服务提升，{len(affected)}组客人满意度+5"}

    # -------------------------------------------------------------------------
    # NPC生成
    # -------------------------------------------------------------------------

    def _generate_day_guests(self) -> list[NPCGroup]:
        """鐢熸垚鏃ラ棿娓稿"""
        return [self._create_day_guest() for _ in range(self._calculate_day_guest_demand())]

    def _generate_overnight_guests(self) -> list[NPCGroup]:
        """鐢熸垚鐩存帴杩囧瀹€備慨澶?#3锛氭帓闄や粖鏃ラ瀹氬笎绡?"""
        return [
            self._create_overnight_guest()
            for _ in range(self._calculate_overnight_guest_demand())
        ]
    def _assign_hidden_tags(self, npc: NPCGroup):
        """分配隐藏标签"""
        npc.economic_level = random.choices([0, 1, 2], weights=[0.3, 0.5, 0.2])[0]
        npc.spending_habit = random.choices([0, 1, 2], weights=[0.3, 0.5, 0.2])[0]
        npc.temperament = random.choices([0, 1, 2], weights=[0.4, 0.4, 0.2])[0]

    def get_day_campsite_remaining(self) -> int:
        """获取当天剩余可接待的日间客组数。"""
        return max(0, self.DAY_CAMPSITE_CAPACITY - self.state.day_campsite_groups_served)

    # -------------------------------------------------------------------------
    # 评价系统
    # -------------------------------------------------------------------------

    def _try_leave_review(self, npc: NPCGroup, result: dict):
        """尝试留评价，并延迟到次日晨间结算。"""
        if random.random() < 0.5:
            rating = self._calculate_rating(npc.total_satisfaction)
            npc.review_rating = rating
            npc.review_left = True
            self.state.pending_reviews.append({
                "created_day": self.state.day,
                "rating": rating,
                "npc_id": npc.id,
                "visit_type": npc.visit_type,
                "group_size": npc.group_size,
            })
            result["events"].append("有客人离场后留下评价，将在次日晨间结算")

    def _settle_pending_reviews(self, result: dict):
        """Turn 1 晨间统一结算前一日及更早产生的评价。"""
        due_reviews = [
            review for review in self.state.pending_reviews
            if review.get("created_day", self.state.day) < self.state.day
        ]
        if not due_reviews:
            return

        ratings = []
        for review in due_reviews:
            rating = int(review["rating"])
            self._apply_review_rating(rating)
            ratings.append(rating)

        self.state.pending_reviews = [
            review for review in self.state.pending_reviews
            if review.get("created_day", self.state.day) >= self.state.day
        ]
        joined_ratings = "、".join(f"{rating}星" for rating in ratings)
        result["events"].append(
            f"晨间结算了{len(ratings)}条昨日评价：{joined_ratings}"
        )

    def _apply_review_rating(self, rating: int):
        self.state.total_reviews += 1
        self.state.total_rating_sum += rating

        if self.state.total_reviews <= 10:
            self.state.reputation_rate = (
                self.state.total_rating_sum / (self.state.total_reviews * 5) * 100
            )
        else:
            self.state.reputation_rate = (
                self.state.reputation_rate * 0.9 + rating * 20 * 0.1
            )

    def _calculate_rating(self, satisfaction: int) -> int:
        if satisfaction >= 90:
            return 5
        elif satisfaction >= 75:
            return 4
        elif satisfaction >= 60:
            return 3
        elif satisfaction >= 45:
            return 2
        else:
            return 1

    # -------------------------------------------------------------------------
    # 辅助方法（修复 #3）
    # -------------------------------------------------------------------------

    def _find_available_tent(self, group_size: int) -> Optional[int]:
        """找合适的空帐篷。修复 #3：排除今日预定帐篷"""
        for tent in self._get_available_unlocked_tents(group_size):
            return tent.id
        return None

    def _has_available_capacity(self) -> bool:
        return any(t.status == "available" for t in self._get_unlocked_tents())

    def _find_npc(self, npc_id: int) -> Optional[NPCGroup]:
        for npc in self.npc_pool:
            if npc.id == npc_id:
                return npc
        return None

    def _get_active_overnight_tent_npcs(self) -> list[NPCGroup]:
        return [
            npc for npc in self.npc_pool
            if npc.visit_type == "overnight"
            and not npc.has_left
            and npc.location.startswith("tent_")
        ]

    def _ensure_checkout_turn(self, npc: NPCGroup) -> Optional[int]:
        if (
            npc.visit_type != "overnight"
            or npc.has_left
            or not npc.location.startswith("tent_")
        ):
            return None
        if npc.checkout_turn in (1, 2):
            return npc.checkout_turn
        npc.checkout_turn = 1 if random.random() < 0.5 else 2
        return npc.checkout_turn

    def get_next_turn_checkout_tents(self) -> list[int]:
        if self.state.turn != 2:
            return []
        tent_ids = {
            int(npc.location.split("_")[1])
            for npc in self._get_active_overnight_tent_npcs()
            if npc.checkout_turn == 2
        }
        return sorted(tent_ids)

    def _has_consumed_dining_today(self, npc: NPCGroup) -> bool:
        return npc.last_dining_day == self.state.day

    def _mark_dining_consumed(self, npc: NPCGroup):
        npc.last_dining_day = self.state.day

    def _get_dining_unit_revenue(self, npc: NPCGroup) -> int:
        facility = self.facilities["dining"]
        return self._calc_spend_amount(
            self.DINING_BASE_PRICE,
            npc.economic_level,
            facility.dining_income_multiplier
        )

    def _get_target_dining_set_menu_key(self, economic_level: int) -> str:
        return self.ECONOMIC_LEVEL_TO_DINING_SET_MENU.get(economic_level, "standard")

    def _get_dining_set_menu_key(self, economic_level: int, dining_level: int) -> str:
        target_key = self._get_target_dining_set_menu_key(economic_level)
        max_available_level = min(dining_level, len(self.DINING_SET_MENU_ORDER) - 1)
        target_index = self.DINING_SET_MENU_ORDER.index(target_key)
        final_index = min(target_index, max_available_level)
        return self.DINING_SET_MENU_ORDER[final_index]

    def _set_next_breakdown(self, tent: Tent):
        if not self._is_tent_unlocked(tent):
            tent.next_breakdown_turn = 0
            return
        base_interval = 15 + tent.level * 5
        interval = random.randint(base_interval, base_interval + 10)
        tent.next_breakdown_turn = self._absolute_turn() + interval

    def _new_day(self, result: Optional[dict] = None):
        """新的一天。修复 #5：绿化衰减逻辑"""
        # 修复 #5：先根据上一日是否处理绿化决定是否衰减
        if not self.state.greenery_processed_today:
            # 上一日没有处理绿化，自动衰减一次
            self._process_greenery_decay()

        self.state.day += 1
        self.state.turn = 1
        # 修复：进入预定日期的新一天时立即启用今日预定状态
        self._assign_reserved_tent_for_today()
        self.state.today_income = {
            "accommodation": 0,
            "campsite": 0,
            "dining": 0,
            "entertainment": 0
        }
        self.state.today_events = []
        self.state.decisions_left = 3
        self.state.pending_turn_plan = None
        # 修复：营业回合故障阻塞标记重置
        self.state.turn_settled = False
        # 修复 #4：防御性清空缓存
        self.state.day_to_overnight_cache.clear()
        self.state.day_campsite_groups_served = 0
        # 重置绿化标记
        self.state.greenery_processed_today = False

        if result is not None:
            self._settle_pending_reviews(result)

        self._generate_daily_reservation()
        self._ensure_today_arrival_plan()

    def _process_greenery_decay(self):
        """绿化衰减"""
        facility = self.facilities["greenery"]
        if facility.level < 2:
            facility.greenery_satisfaction = max(
                0, facility.greenery_satisfaction - facility.greenery_decay_rate
            )

    def _generate_daily_reservation(self):
        """生成每日预定。修复 #2 #3：有待处理请求或已确认预定时不生成"""
        if self.state.reservation is not None or self.state.reserved_tent_id is not None:
            return

        if random.random() < 0.3:
            group_size = random.randint(1, 5)
            # 修复：预定时保存三个隐藏标签，入住时恢复
            self.state.reservation = {
                "group_size": group_size,
                "economic_level": random.randint(0, 2),
                "spending_habit": random.randint(0, 2),
                "temperament": random.randint(0, 2)
            }

    # -------------------------------------------------------------------------
    # 状态查询
    # -------------------------------------------------------------------------

    def get_full_state(self) -> dict:
        # 修复：对外隐藏NPC隐藏标签，引擎内部数据不变
        safe_npcs = []
        for n in self.npc_pool:
            if not n.has_left:
                safe_npcs.append({
                    "id": n.id,
                    "group_size": n.group_size,
                    "visit_type": n.visit_type,
                    "arrival_turn": n.arrival_turn,
                    "location": n.location,
                    "total_satisfaction": n.total_satisfaction,
                    "has_left": n.has_left,
                    "review_left": n.review_left,
                    "review_rating": n.review_rating,
                    "visit_count": n.visit_count,
                    "last_visit_day": n.last_visit_day,
                    "is_reserved": n.is_reserved,
                    "paid": n.paid
                })

        # 修复：对外预定只保留 group_size 和 status，不暴露隐藏标签
        if self.state.reservation is not None:
            safe_reservation = {
                "group_size": self.state.reservation["group_size"],
                "status": "accepted" if self.state.reserved_tent_id is not None else "pending"
            }
        else:
            safe_reservation = None

        # 修复：对外只暴露帐篷必要字段，隐藏 next_breakdown_turn / satisfaction_bonus
        safe_tents = {
            tid: {
                "id": t.id,
                "capacity": t.capacity,
                "unlocked": t.is_unlocked,
                "level": t.level,
                "status": t.status,
                "occupied_by": t.occupied_by
            }
            for tid, t in self.tents.items()
        }

        return {
            "day": self.state.day,
            "turn": self.state.turn,
            "balance": self.state.balance,
            "reputation_rate": round(self.state.reputation_rate, 1),
            "tents": safe_tents,
            "facilities": {k: asdict(v) for k, v in self.facilities.items()},
            "active_npcs": safe_npcs,
            "reservation": safe_reservation,
            "decisions_left": self.state.decisions_left,
            "food_stock": self.state.food_stock,
            "today_income": self.state.today_income
        }

    def _get_tents_summary(self) -> dict:
        return {tid: {
            "status": t.status,
            "unlocked": t.is_unlocked,
            "level": t.level,
            "occupied_by": t.occupied_by,
            "capacity": t.capacity
        } for tid, t in self.tents.items()}

    def _get_npcs_summary(self) -> list[dict]:
        return [{
            "id": n.id,
            "group_size": n.group_size,
            "location": n.location,
            "visit_type": n.visit_type,
            "satisfaction": n.total_satisfaction
        } for n in self.npc_pool if not n.has_left]

    def get_state_for_display(self) -> str:
        state = self.get_full_state()
        lines = [
            f"📍 第{state['day']}天 · 回合{state['turn']}",
            f"💰 余额: {state['balance']}金币",
            f"⭐ 好评率: {state['reputation_rate']}%",
            f"🎯 剩余决策点: {state['decisions_left']}",
            "",
            "--- 帐篷状态 ---"
        ]
        for tid, tent in state["tents"].items():
            si = {"available": "🟢", "occupied": "🔴",
                  "cleaning": "🟡", "broken": "⚠️", "reserved": "🔵"}.get(tent["status"], "❓")
            line = f"  {tid}号: {si} Lv.{tent['level']} 容量{tent['capacity']}人"
            if tent["occupied_by"]:
                line += " (有客人)"
            lines.append(line)

        lines.extend([
            "",
            "--- 设施 ---",
            f"  餐饮区 Lv.{state['facilities']['dining']['level']}",
            f"  娱乐区 Lv.{state['facilities']['entertainment']['level']}",
            f"  绿化 Lv.{state['facilities']['greenery']['level']} (环境满意度+{state['facilities']['greenery']['greenery_satisfaction']:.1f})",
            "",
            "--- 今日收入 ---",
            f"  住宿: +{state['today_income']['accommodation']}",
            f"  营位: +{state['today_income']['campsite']}",
            f"  餐饮: +{state['today_income']['dining']}",
            f"  娱乐: +{state['today_income']['entertainment']}"
        ])
        return "\n".join(lines)
