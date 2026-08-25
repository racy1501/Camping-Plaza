#!/usr/bin/env python3
"""《露营广场》Balance Simulator v1.

本工具只驱动正式 CampingPlazaEngine；每个 run 使用独立临时 SQLite 存档。
默认只打印摘要，--output 才会写入 JSON 或 CSV。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import shutil
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SIM_TEMP_ROOT = Path(tempfile.gettempdir()) / "camping-plaza-balance-simulator"
if str(ROOT / "camping_plaza") not in sys.path:
    sys.path.insert(0, str(ROOT / "camping_plaza"))

from game_engine import CampingPlazaEngine  # noqa: E402


STRATEGIES = ("growth_priority", "balanced", "quality_priority")
REPAYMENT_BEHAVIORS = ("eager_repayment", "deadline_aware")
CHECKPOINT_DAYS = (15, 17, 20, 25, 30)
REAL_DAY17 = {
    "gold": 5050,
    "average_rating": 3.2826,
    "review_count": 46,
    "cumulative_service_groups": 109,
    "tents": "1-4",
    "dining_level": 1,
    "entertainment_level": 2,
    "greenery_level": 2,
}
PROJECT_IDS = (
    "tent_2", "tent_3", "tent_4", "tent_5", "tent_6",
    "dining_lv1", "dining_lv2", "entertainment_lv1", "entertainment_lv2",
    "greenery_lv1", "greenery_lv2", "hot_spring",
)
ECONOMIC_CANDIDATES = {
    "repayment_deadline_day": (28, 30, 35),
}
HOT_SPRING_TIMING_FALLBACK_DAY = {"early": 15, "mid": 18}
REPRESENTATIVE_ECONOMIC_SCENARIOS: dict[str, dict[str, int | str] | None] = {
    "A": {
        "initial_debt": 15000, "repayment_deadline_day": 30,
        "lodging_consumable_rate": 0.10,
        "hot_spring_base_maintenance_cost": 100,
        "hot_spring_variable_cost_rate": 0.20, "hot_spring_timing": "current",
    },
    "B": {
        "initial_debt": 20000, "repayment_deadline_day": 30,
        "lodging_consumable_rate": 0.10,
        "hot_spring_base_maintenance_cost": 100,
        "hot_spring_variable_cost_rate": 0.20, "hot_spring_timing": "mid",
    },
    "C": {
        "initial_debt": 25000, "repayment_deadline_day": 30,
        "lodging_consumable_rate": 0.10,
        "hot_spring_base_maintenance_cost": 100,
        "hot_spring_variable_cost_rate": 0.20, "hot_spring_timing": "early",
    },
    # Control deliberately does not create an overlay instance. It must take
    # the unchanged formal-simulator path.
    "D": None,
}


class EconomicOverlay:
    """Simulator-only debt and cost ledger attached to one temporary engine."""

    def __init__(self, config: dict[str, int | str]):
        self.config = dict(config)
        self.initial_debt = int(config["initial_debt"])
        self.debt_remaining = self.initial_debt
        self.debt_repaid_total = 0
        self.repayment_deadline_day = int(config["repayment_deadline_day"])
        self.lodging_consumable_rate = float(config["lodging_consumable_rate"])
        self.hot_spring_base_maintenance_cost = float(config["hot_spring_base_maintenance_cost"])
        self.hot_spring_variable_cost_rate = float(config["hot_spring_variable_cost_rate"])
        self.first_repayment_day: int | None = None
        self.debt_paid_off_day: int | None = None
        self.overdue_amount_at_deadline: int | None = None
        self.cumulative_lodging_consumable_cost = 0
        self.cumulative_hot_spring_operating_cost = 0
        self.cumulative_debt_repayment = 0
        self.daily_lodging_consumable_cost = 0
        self.daily_hot_spring_operating_cost = 0
        self.daily_debt_repayment = 0

    def begin_day(self) -> None:
        self.daily_lodging_consumable_cost = 0
        self.daily_hot_spring_operating_cost = 0
        self.daily_debt_repayment = 0

    def settle_turn6_auto_costs(self, engine: CampingPlazaEngine, strategy: str, scenario: str) -> None:
        """在 Turn 5 -> Turn 6 后一次性结算当天新增的实验经营成本。"""
        current_day = engine.state.day
        active_npcs = {
            npc.id: npc for npc in engine.npc_pool
            if not npc.has_left and npc.visit_type == "overnight"
        }
        lodging_cost = 0
        seen_npcs: set[str] = set()
        for entry in engine.state.today_arrival_plan:
            if entry.get("planned_day") != current_day or entry.get("arrival_status") != "arrived":
                continue
            npc_id = entry.get("npc_id")
            npc = active_npcs.get(npc_id)
            if npc is None or npc_id in seen_npcs:
                continue
            tent_id = next(
                (tent.id for tent in engine.tents.values() if tent.occupied_by == npc_id),
                None,
            )
            if tent_id is None or not str(npc.location).startswith("tent_"):
                continue
            seen_npcs.add(npc_id)
            lodging_cost += engine.TENT_PRICES[tent_id] * self.lodging_consumable_rate

        lodging_cost = int(math.floor(lodging_cost + 0.5))
        if engine.state.hot_spring_built:
            hot_spring_gross = engine.state.today_income.get("hot_spring", 0)
            spring_cost = int(math.floor(
                self.hot_spring_base_maintenance_cost
                + hot_spring_gross * self.hot_spring_variable_cost_rate
                + 0.5
            ))
        else:
            spring_cost = 0
        total_cost = lodging_cost + spring_cost
        if total_cost > engine.state.balance:
            raise RuntimeError(
                "economic overlay automatic cost exceeds balance: "
                f"day={current_day} strategy={strategy} scenario={scenario} "
                f"balance={engine.state.balance} lodging_consumable={lodging_cost} "
                f"hot_spring_operating={spring_cost} total={total_cost}"
            )
        engine.state.balance -= total_cost
        self.daily_lodging_consumable_cost = lodging_cost
        self.daily_hot_spring_operating_cost = spring_cost
        self.cumulative_lodging_consumable_cost += lodging_cost
        self.cumulative_hot_spring_operating_cost += spring_cost

    def repay_after_day_end(
        self, engine: CampingPlazaEngine, strategy: str,
        repayment_behavior: str = "deadline_aware",
    ) -> dict[str, Any]:
        if self.debt_remaining <= 0:
            return {"amount": 0, "urgency_stage": "paid_off", "investment_reserve": 0,
                    "next_growth_target": None}
        decision = repayment_decision(engine, self, strategy, repayment_behavior)
        amount = decision["amount"]
        if amount <= 0:
            return decision
        engine.state.balance -= amount
        self.debt_remaining -= amount
        self.debt_repaid_total += amount
        self.cumulative_debt_repayment += amount
        self.daily_debt_repayment += amount
        if self.first_repayment_day is None:
            self.first_repayment_day = engine.state.day
        if self.debt_remaining == 0 and self.debt_paid_off_day is None:
            self.debt_paid_off_day = engine.state.day
        return decision

    def capture_deadline(self, day: int) -> None:
        if day == self.repayment_deadline_day and self.overdue_amount_at_deadline is None:
            self.overdue_amount_at_deadline = self.debt_remaining


def validate_economic_config(config: dict[str, int | str]) -> dict[str, int | str]:
    normalized = dict(config)
    initial_debt = int(normalized["initial_debt"])
    if initial_debt < 0:
        raise ValueError(f"initial_debt must be a non-negative integer, got {initial_debt}")
    normalized["initial_debt"] = initial_debt
    for key in (
        "lodging_consumable_rate",
        "hot_spring_base_maintenance_cost",
        "hot_spring_variable_cost_rate",
    ):
        value = float(normalized[key])
        if value < 0:
            raise ValueError(f"{key} must be non-negative, got {value}")
        normalized[key] = value
    for key, allowed in ECONOMIC_CANDIDATES.items():
        value = int(normalized[key])
        if value not in allowed:
            raise ValueError(f"{key} must be one of {allowed}, got {value}")
        normalized[key] = value
    timing = str(normalized["hot_spring_timing"])
    if timing not in {"early", "mid", "current"}:
        raise ValueError("hot_spring_timing must be early, mid, or current")
    normalized["hot_spring_timing"] = timing
    return normalized


def apply_hot_spring_timing_overlay(engine: CampingPlazaEngine, timing: str) -> None:
    """Instance-only catalog copy: change only hot-spring fallback availability."""
    if timing == "current":
        return
    fallback_day = HOT_SPRING_TIMING_FALLBACK_DAY[timing]
    catalog = []
    for project in engine.GROWTH_PROJECT_CATALOG:
        copied = dict(project)
        if copied["project_id"] == "hot_spring":
            copied["fallback_operating_day"] = fallback_day
        catalog.append(copied)
    engine.GROWTH_PROJECT_CATALOG = tuple(catalog)


def install_economic_overlay_hooks(engine: CampingPlazaEngine, overlay: EconomicOverlay) -> None:
    """只保留实例级温泉购买时点 overlay；成本在 Turn 6 统一结算。"""


def next_growth_target(engine: CampingPlazaEngine) -> dict[str, Any] | None:
    """复用正式 catalog 与现有策略购买顺序，找下一未完成成长目标。"""
    catalog = {item["project_id"]: item for item in engine.get_growth_project_catalog()}
    for project_id in PROJECT_IDS:
        item = catalog.get(project_id)
        if item is not None and not item["completed"]:
            return item
    return None


def debt_repayment_decision(
    engine: CampingPlazaEngine, overlay: EconomicOverlay, strategy: str,
) -> dict[str, Any]:
    """用成长储备和期限进度形成一次模拟器专用的偿债决策。"""
    days_until_deadline = max(0, overlay.repayment_deadline_day - engine.state.day)
    repayment_window = max(1, overlay.repayment_deadline_day - 1)
    elapsed_ratio = (engine.state.day - 1) / repayment_window
    if elapsed_ratio < 1 / 3:
        urgency_stage = "low"
    elif elapsed_ratio < 2 / 3:
        urgency_stage = "medium"
    else:
        urgency_stage = "high"

    operating_reserve = {"growth_priority": 300, "balanced": 700, "quality_priority": 1200}[strategy]
    profiles = {
        "growth_priority": {
            "low": (1.25, 1.00, 0.15), "medium": (0.60, 0.80, 0.45), "high": (0.15, 0.00, 0.90),
        },
        "balanced": {
            "low": (0.75, 1.00, 0.20), "medium": (0.45, 0.50, 0.55), "high": (0.10, 0.00, 0.90),
        },
        "quality_priority": {
            "low": (0.55, 1.00, 0.15), "medium": (0.35, 0.50, 0.45), "high": (0.10, 0.00, 0.80),
        },
    }
    investment_factor, development_buffer_factor, surplus_fraction = profiles[strategy][urgency_stage]
    target = next_growth_target(engine)
    target_price = int(target["price"]) if target is not None else 0
    investment_reserve = math.ceil(target_price * investment_factor)
    development_buffer = math.ceil(operating_reserve * development_buffer_factor)
    protected_cash = operating_reserve + investment_reserve + development_buffer
    discretionary_cash = max(0, engine.state.balance - protected_cash)
    amount = 0 if urgency_stage == "low" else int(discretionary_cash * surplus_fraction)

    # 临近期限时，用剩余还款窗口校正节奏；低紧迫阶段只使用真正多余的现金。
    remaining_payment_slots = max(1, days_until_deadline + 1)
    required_daily_payment = math.ceil(overlay.debt_remaining / remaining_payment_slots)
    if urgency_stage == "medium":
        amount = max(amount, min(max(0, engine.state.balance - operating_reserve), required_daily_payment))
    elif urgency_stage == "high":
        amount = max(amount, min(max(0, engine.state.balance - operating_reserve), required_daily_payment))

    amount = min(amount, overlay.debt_remaining, engine.state.balance)
    return {
        "amount": amount,
        "urgency_stage": urgency_stage,
        "days_until_deadline": days_until_deadline,
        "investment_reserve": investment_reserve,
        "next_growth_target": target["project_id"] if target is not None else None,
        "next_growth_target_price": target_price,
        "next_growth_target_ready": (
            bool(target["prerequisite_met"] and target["operation_requirement_met"])
            if target is not None else False
        ),
    }


def repayment_decision(
    engine: CampingPlazaEngine, overlay: EconomicOverlay, strategy: str,
    repayment_behavior: str,
) -> dict[str, Any]:
    if repayment_behavior == "deadline_aware":
        return debt_repayment_decision(engine, overlay, strategy)
    if repayment_behavior != "eager_repayment":
        raise ValueError(f"unknown repayment_behavior: {repayment_behavior}")
    reserve = {"growth_priority": 300, "balanced": 700, "quality_priority": 1200}[strategy]
    amount = min(overlay.debt_remaining, max(0, engine.state.balance - reserve))
    return {
        "amount": amount,
        "urgency_stage": "eager",
        "days_until_deadline": max(0, overlay.repayment_deadline_day - engine.state.day),
        "investment_reserve": 0,
        "next_growth_target": None,
        "next_growth_target_price": 0,
        "next_growth_target_ready": False,
    }


def formal_repayment_amount(
    engine: CampingPlazaEngine, strategy: str, available: int,
    repayment_behavior: str,
) -> int:
    """为正式 debt_remaining 计算模拟行动，不创建第二套债务账本。"""
    if repayment_behavior == "eager_repayment":
        reserve = {"growth_priority": 300, "balanced": 700, "quality_priority": 1200}[strategy]
        return min(engine.state.debt_remaining, max(0, available - reserve))
    if repayment_behavior != "deadline_aware":
        raise ValueError(f"unknown repayment_behavior: {repayment_behavior}")

    deadline = engine.state.repayment_deadline_day
    elapsed_ratio = (engine.state.day - 1) / max(1, deadline - 1)
    urgency_stage = "low" if elapsed_ratio < 1 / 3 else "medium" if elapsed_ratio < 2 / 3 else "high"
    operating_reserve = {"growth_priority": 300, "balanced": 700, "quality_priority": 1200}[strategy]
    profiles = {
        "growth_priority": {"low": (1.25, 1.00, 0.15), "medium": (0.60, 0.80, 0.45), "high": (0.15, 0.00, 0.90)},
        "balanced": {"low": (0.75, 1.00, 0.20), "medium": (0.45, 0.50, 0.55), "high": (0.10, 0.00, 0.90)},
        "quality_priority": {"low": (0.55, 1.00, 0.15), "medium": (0.35, 0.50, 0.45), "high": (0.10, 0.00, 0.80)},
    }
    investment_factor, buffer_factor, surplus_fraction = profiles[strategy][urgency_stage]
    target = next_growth_target(engine)
    target_price = int(target["price"]) if target is not None else 0
    protected_cash = (
        operating_reserve
        + math.ceil(target_price * investment_factor)
        + math.ceil(operating_reserve * buffer_factor)
    )
    amount = 0 if urgency_stage == "low" else int(max(0, available - protected_cash) * surplus_fraction)
    if urgency_stage in {"medium", "high"}:
        required_daily_payment = math.ceil(
            engine.state.debt_remaining / max(1, deadline - engine.state.day + 1)
        )
        amount = max(amount, min(max(0, available - operating_reserve), required_daily_payment))
    return min(amount, engine.state.debt_remaining, available)


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "median": None, "p90": None}
    ordered = sorted(values)
    def pick(p: float) -> float:
        return ordered[round((len(ordered) - 1) * p)]
    return {"p10": pick(0.10), "median": pick(0.50), "p90": pick(0.90)}


def review_average(ratings: list[int], window: int | None = None) -> float | None:
    """按正式已结算评价计算滚动或全历史平均；仅供模拟报告使用。"""
    if not ratings:
        return None
    selected = ratings[-window:] if window is not None else ratings
    return sum(selected) / len(selected)


def unlocked_tent_count(engine: CampingPlazaEngine) -> int:
    return sum(1 for tent in engine.tents.values() if tent.is_unlocked)


def planned_food_need(engine: CampingPlazaEngine) -> int:
    return sum(
        entry["group_size"]
        for entry in engine.state.today_arrival_plan
        for action in entry.get("planned_actions", [])
        if action.get("action") == "dining"
        and action.get("status") in {"pending", "waiting_for_restock"}
        and entry.get("arrival_status") != "turned_away_full"
    )


def food_package_for_gap(engine: CampingPlazaEngine, reserve: int) -> str | None:
    gap = max(0, planned_food_need(engine) + reserve - engine.state.food_stock)
    if gap <= 0:
        return None
    for key in ("small", "medium", "large"):
        package = engine.FOOD_PACKAGES[key]
        if package["portions"] >= gap and engine.state.balance >= package["price"]:
            return key
    return "large" if engine.state.balance >= engine.FOOD_PACKAGES["large"]["price"] else None


def broken_tent_ids(engine: CampingPlazaEngine) -> list[int]:
    return [tid for tid, tent in engine.tents.items() if tent.is_unlocked and tent.status == "broken"]


def turn_actions(engine: CampingPlazaEngine, strategy: str) -> tuple[list[dict], list[dict]]:
    """返回本 Turn 的 free_actions / decision actions；不重写任何正式结算逻辑。"""
    free = [{"action": "clean_tents", "tent_ids": list(engine.tents)}]
    actions: list[dict] = []
    food_reserve = {"growth_priority": 0, "balanced": 2, "quality_priority": 4}[strategy]
    food = food_package_for_gap(engine, food_reserve)
    if food:
        actions.append({"action": "buy_food_package", "package_key": food})
    for tent_id in broken_tent_ids(engine):
        if len(actions) >= 3:
            break
        if engine.state.balance >= engine.REPAIR_COST:
            actions.append({"action": "repair_tent", "tent_id": tent_id})
    if strategy == "quality_priority":
        if engine.state.turn in (2, 3, 4) and len(actions) < 3:
            actions.append({"action": "clean_campsite"})
        if engine.state.turn in (2, 3, 4) and len(actions) < 3:
            actions.append({"action": "improve_service"})
    elif strategy == "balanced":
        if engine.state.turn in (3, 4) and len(actions) < 3:
            actions.append({"action": "clean_campsite"})
        if engine.state.turn == 4 and len(actions) < 3:
            actions.append({"action": "improve_service"})
    else:
        if engine.state.turn == 4 and len(actions) < 3:
            actions.append({"action": "make_post"})
    return free, actions[:engine.state.decisions_left]


def day_end_actions(
    engine: CampingPlazaEngine, strategy: str,
    economic_config: dict[str, int | str] | None = None,
    repayment_unlock_day: int = 1,
    repayment_behavior: str = "deadline_aware",
) -> list[dict]:
    """用正式 catalog 判断日终动作；默认使用正式债务状态。"""
    actions: list[dict] = []
    available = engine.state.balance
    balance_buffer = {"growth_priority": 0, "balanced": 250, "quality_priority": 500}[strategy]
    # Greenery maintenance is a prerequisite for its formal growth projects.
    # Expansion-oriented agents perform only the minimum needed to unlock it;
    # the other agents maintain it as part of normal operating quality.
    if strategy in STRATEGIES:
        greenery = engine.facilities["greenery"]
        needs_greenery_progress = greenery.level < 2
        maintenance_due = greenery.greenery_satisfaction < engine.GREENERY_LEVEL_MAX[greenery.level]
        if (needs_greenery_progress or (strategy != "growth_priority" and maintenance_due)) and available >= 50 + balance_buffer:
            actions.append({"action": "manage_greenery", "params": {"action": "maintain"}})
            available -= 50
    catalog = {item["project_id"]: item for item in engine.get_growth_project_catalog()}
    for project_id in PROJECT_IDS:
        item = catalog.get(project_id)
        if not item or not item["can_purchase_now"]:
            continue
        if available - item["price"] < balance_buffer:
            continue
        actions.append({"action": "purchase_growth_project", "params": {"project_id": project_id}})
        available -= item["price"]
        # catalog changes after purchase; day-end executor validates each action again.
        if strategy != "growth_priority":
            break
    if strategy == "quality_priority" and not any(a["action"] == "manage_greenery" for a in actions):
        greenery = engine.facilities["greenery"]
        if greenery.level < 2 and available >= 50:
            actions.insert(0, {"action": "manage_greenery", "params": {"action": "maintain"}})
            available -= 50
    food = food_package_for_gap(engine, {"growth_priority": 0, "balanced": 2, "quality_priority": 4}[strategy])
    if food and engine.FOOD_PACKAGES[food]["price"] <= available:
        actions.append({"action": "buy_food_package", "params": {"package_key": food}})
        available -= engine.FOOD_PACKAGES[food]["price"]
    for tent_id in broken_tent_ids(engine):
        if available >= engine.REPAIR_COST:
            actions.append({"action": "repair_tent", "params": {"tent_id": tent_id}})
            available -= engine.REPAIR_COST
    if economic_config is None and engine.state.day >= repayment_unlock_day:
        repayment = formal_repayment_amount(
            engine, strategy, available, repayment_behavior,
        )
        if repayment:
            actions.append({"action": "repay_debt", "params": {"amount": repayment}})
    return actions


def day_snapshot(
    engine: CampingPlazaEngine, satisfaction: Counter[int], review_stars: Counter[int],
    overlay: EconomicOverlay | None = None,
) -> dict[str, Any]:
    state = engine.state
    arrived = [e for e in state.today_arrival_plan if e.get("arrival_status") == "arrived"]
    turned = [e for e in state.today_arrival_plan if e.get("arrival_status") == "turned_away_full"]
    day_turned = [e for e in turned if e.get("visit_type") == "day"]
    overnight_turned = [e for e in turned if e.get("visit_type") == "overnight"]
    max_tent_capacity = max((tent.capacity for tent in engine.tents.values() if tent.is_unlocked), default=0)
    unsuitable_overnight = [e for e in overnight_turned if e.get("group_size", 0) > max_tent_capacity]
    shortage_actions = [
        action for entry in state.today_arrival_plan for action in entry.get("planned_actions", [])
        if action.get("result") == "insufficient_food"
    ]
    profile = state.daily_demand_profile or {}
    income = sum(state.today_income.values())
    expense = sum(state.today_expenses.values())
    overlay_expense = 0
    debt_remaining = None
    if overlay is not None:
        overlay_expense = (
            overlay.daily_lodging_consumable_cost
            + overlay.daily_hot_spring_operating_cost
            + overlay.daily_debt_repayment
        )
        debt_remaining = overlay.debt_remaining if overlay.initial_debt > 0 else None
    return {
        "day": state.day, "gold": state.balance,
        "average_rating": engine.get_average_rating(), "review_count": state.total_reviews,
        "rating_recent_10": review_average(
            [int(review["rating"]) for review in state.review_history], 10
        ),
        "rating_all_history": review_average(
            [int(review["rating"]) for review in state.review_history]
        ),
        "stars_1": review_stars[1], "stars_2": review_stars[2], "stars_3": review_stars[3], "stars_4": review_stars[4], "stars_5": review_stars[5],
        "daytime_natural_demand": profile.get("natural_day_group_demand", 0),
        "overnight_natural_demand": profile.get("natural_overnight_group_demand", 0),
        "daytime_served": sum(e.get("visit_type") == "day" for e in arrived),
        "overnight_served": sum(e.get("visit_type") == "overnight" for e in arrived),
        "turned_away_full": len(turned), "turned_away_day": len(day_turned),
        "turned_away_overnight": len(overnight_turned),
        "turned_away_no_suitable_tent": len(unsuitable_overnight),
        "food_shortage": len(shortage_actions),
        "food_shortage_groups": len(shortage_actions),
        "daily_income": income, "daily_expense": expense + overlay_expense,
        "daily_net": income - expense - overlay_expense,
        "formal_daily_expense": expense, "overlay_daily_expense": overlay_expense,
        "debt_remaining": debt_remaining,
        "cumulative_service_groups": state.total_served_groups,
        "completed_growth_nodes": engine.get_growth_progress()["completed_growth_nodes"],
        "dining_success": state.successful_dining_groups,
        "entertainment_success": state.successful_paid_entertainment_groups,
        "greenery_satisfaction": engine.facilities["greenery"].greenery_satisfaction,
        "unlocked_tent_count": unlocked_tent_count(engine),
        "dining_level": engine.facilities["dining"].level,
        "entertainment_level": engine.facilities["entertainment"].level,
        "greenery_level": engine.facilities["greenery"].level,
        "hot_spring_built": state.hot_spring_built,
        "satisfaction_distribution": dict(sorted(satisfaction.items())),
    }


def simulate_run(
    days: int, seed: int, strategy: str,
    economic_config: dict[str, int | str] | None = None,
    repayment_behavior: str = "deadline_aware",
    repayment_unlock_day: int = 1,
    review_five_star_threshold: int = 84,
    capture_telemetry: bool = False,
) -> dict[str, Any]:
    random.seed(seed)
    SIM_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="run-", dir=SIM_TEMP_ROOT)
    engine = None
    try:
        engine = CampingPlazaEngine(str(Path(temp_dir) / "run.sqlite"))
        if review_five_star_threshold != 84:
            original_calculate_rating = engine._calculate_rating

            def calculate_rating_with_simulated_five_star_threshold(
                satisfaction: float,
            ) -> int:
                if satisfaction >= review_five_star_threshold:
                    return 5
                return original_calculate_rating(satisfaction)

            engine._calculate_rating = calculate_rating_with_simulated_five_star_threshold  # type: ignore[method-assign]
        overlay = None
        if economic_config is not None:
            if repayment_behavior not in REPAYMENT_BEHAVIORS:
                raise ValueError(f"repayment_behavior must be one of {REPAYMENT_BEHAVIORS}")
            normalized_config = validate_economic_config(economic_config)
            overlay = EconomicOverlay(normalized_config)
            apply_hot_spring_timing_overlay(engine, str(normalized_config["hot_spring_timing"]))
            install_economic_overlay_hooks(engine, overlay)
        final_satisfaction: Counter[int] = Counter()
        review_stars: Counter[int] = Counter()
        review_events: list[dict[str, Any]] = []
        telemetry: dict[str, list[dict[str, Any]]] = {
            "turns": [], "day_end": [], "conflicts": [],
        }
        active_trace: dict[str, Any] | None = None
        tent_failures = 0
        original_review = engine._try_leave_review
        def capture_review(npc: Any, result: dict) -> None:
            final_satisfaction[int(round(npc.total_satisfaction))] += 1
            original_review(npc, result)
            if npc.review_left:
                review_stars[int(npc.review_rating)] += 1
                positive_tags, negative_tags = engine._get_review_comment_candidates(npc)
                review_events.append({
                    "created_day": engine.state.day,
                    "rating": int(npc.review_rating),
                    "score": engine._get_review_score(npc),
                    "satisfaction": npc.total_satisfaction,
                    "positive_total": npc.positive_experience_total,
                    "negative_total": npc.negative_experience_total,
                    "positive_tags": positive_tags,
                    "negative_tags": negative_tags,
                })
        engine._try_leave_review = capture_review  # type: ignore[method-assign]
        original_breakdowns = engine._handle_breakdowns
        def capture_breakdowns(result: dict) -> None:
            nonlocal tent_failures
            before = {tid: tent.status for tid, tent in engine.tents.items()}
            original_breakdowns(result)
            tent_failures += sum(
                before.get(tid) != "broken" and tent.status == "broken"
                for tid, tent in engine.tents.items()
            )
        engine._handle_breakdowns = capture_breakdowns  # type: ignore[method-assign]
        original_arrival_processor = engine._process_planned_arrivals

        def capture_planned_arrivals(result: dict) -> None:
            before = {
                entry.get("npc_id"): entry.get("arrival_status")
                for entry in engine.state.today_arrival_plan
                if entry.get("planned_day") == engine.state.day
                and entry.get("arrival_turn") == engine.state.turn
            }
            original_arrival_processor(result)
            if (
                active_trace is not None
                and engine.state.turn == active_trace["turn"]
            ):
                active_trace["arrival_ids_processed_during_checkin"] = [
                    entry["npc_id"]
                    for entry in engine.state.today_arrival_plan
                    if entry.get("planned_day") == engine.state.day
                    and entry.get("arrival_turn") == engine.state.turn
                    and before.get(entry.get("npc_id")) == "pending"
                    and entry.get("arrival_status") == "arrived"
                ]

        engine._process_planned_arrivals = capture_planned_arrivals  # type: ignore[method-assign]
        original_checkin = engine._process_checkin

        def capture_checkin(result: dict) -> None:
            if (
                active_trace is not None
                and engine.state.turn == active_trace["turn"]
            ):
                active_trace.setdefault("execution_order", []).append("checkin")
            original_checkin(result)

        engine._process_checkin = capture_checkin  # type: ignore[method-assign]
        original_clean_campsite = engine.clean_campsite

        def capture_clean_campsite(*, consume_decision: bool = True) -> dict:
            trace = active_trace
            if trace is not None:
                trace.setdefault("execution_order", []).append("clean_campsite")
                trace["clean_candidate_ids"] = [
                    npc.id for npc in engine.npc_pool if not npc.has_left
                ]
                trace["arrival_ids_before_clean"] = [
                    npc.id
                    for npc in engine.npc_pool
                    if not npc.has_left and npc.arrival_turn == engine.state.turn
                ]
            action_result = original_clean_campsite(consume_decision=consume_decision)
            if trace is not None:
                trace["clean_success"] = bool(action_result.get("success"))
                trace["clean_judged_ids"] = (
                    list(trace.get("clean_candidate_ids", []))
                    if action_result.get("success")
                    else []
                )
                trace["clean_affected_ids"] = list(
                    action_result.get("affected_npc_ids", [])
                )
            return action_result

        engine.clean_campsite = capture_clean_campsite  # type: ignore[method-assign]
        daily: list[dict[str, Any]] = []
        purchases: dict[str, int | None] = {project: None for project in PROJECT_IDS}
        qualification_days: dict[str, int | None] = {project: None for project in PROJECT_IDS}
        formal_repayment_trace: list[dict[str, int | bool]] = []
        economic_trace: list[dict[str, Any]] = []
        hot_spring_eligible_day: int | None = None
        tent_repairs = 0
        while engine.state.day <= days:
            if overlay is not None:
                overlay.begin_day()
            while engine.state.turn <= 5:
                event = engine.get_current_temporary_conflict_event()
                if event is not None:
                    conflict_result = engine.resolve_current_temporary_conflict("verbal")
                    if capture_telemetry:
                        telemetry["conflicts"].append({
                            "day": engine.state.day,
                            "turn": engine.state.turn,
                            "success": conflict_result.get("success", False),
                            "message": conflict_result.get("message", ""),
                        })
                if engine.state.turn in (2, 3, 4, 5):
                    free, actions = turn_actions(engine, strategy)
                    submitted = engine.submit_turn_plan(free, actions)
                    if not submitted.get("success"):
                        raise RuntimeError(f"turn plan rejected: {submitted}")
                    if capture_telemetry:
                        telemetry["turns"].append({
                            "day": engine.state.day,
                            "turn": engine.state.turn,
                            "decisions_before": engine.state.decisions_left + len(actions),
                            "planned_actions": [item["action"] for item in actions],
                            "planned_free_actions": [item["action"] for item in free],
                            "broken_before": broken_tent_ids(engine),
                            "food_risk_before": (
                                engine.state.food_stock < planned_food_need(engine)
                            ),
                            "execution_order": ["plan_submitted"],
                        })
                    active_trace = telemetry["turns"][-1] if capture_telemetry else None
                advanced = engine.advance_turn()
                if capture_telemetry and engine.state.turn in (3, 4, 5, 6):
                    executed_turn = engine.state.turn - 1
                    if executed_turn in (2, 3, 4, 5) and telemetry["turns"]:
                        trace = telemetry["turns"][-1]
                        if trace["day"] == engine.state.day and trace["turn"] == executed_turn:
                            execution = advanced.get("plan_execution", {})
                            trace["executed_actions"] = [
                                {
                                    "action": item.get("action"),
                                    "success": bool(item.get("success")),
                                    "affected_count": len(item.get("affected_npc_ids", [])),
                                    "message": item.get("message", ""),
                                }
                                for item in (
                                    execution.get("free_actions", [])
                                    + execution.get("actions", [])
                                )
                            ]
                active_trace = None
            if overlay is not None:
                overlay.settle_turn6_auto_costs(
                    engine,
                    strategy,
                    str((economic_config or {}).get("scenario", "custom")),
                )
            catalog_before = engine.get_growth_project_catalog()
            for project in catalog_before:
                project_id = project["project_id"]
                if (
                    qualification_days[project_id] is None
                    and project["prerequisite_met"]
                    and project["operation_requirement_met"]
                ):
                    qualification_days[project_id] = engine.state.day
            hot = next(x for x in catalog_before if x["project_id"] == "hot_spring")
            if hot_spring_eligible_day is None and hot["prerequisite_met"] and hot["operation_requirement_met"]:
                hot_spring_eligible_day = engine.state.day
            balance_before_day_end = engine.state.balance
            debt_before_day_end = engine.state.debt_remaining
            result = engine.submit_day_end_actions(day_end_actions(
                engine,
                strategy,
                economic_config,
                repayment_unlock_day,
                repayment_behavior,
            ))
            if not result.get("success"):
                raise RuntimeError(f"day end rejected: {result}")
            if capture_telemetry:
                telemetry["day_end"].append({
                    "day": engine.state.day,
                    "results": [
                        {
                            "action": item.get("action"),
                            "success": bool(item.get("success")),
                            "message": item.get("message", ""),
                            "project_id": (item.get("params") or {}).get("project_id"),
                        }
                        for item in result.get("results", [])
                    ],
                })
            for item in result.get("results", []):
                if item.get("success") and item.get("action") == "repair_tent":
                    tent_repairs += 1
                if item.get("success") and item.get("action") == "purchase_growth_project":
                    project = (item.get("params") or {}).get("project_id")
                    if project in purchases and purchases[project] is None:
                        purchases[project] = engine.state.day
            formal_repayment_trace.append({
                "day": engine.state.day,
                "balance_before_day_end": balance_before_day_end,
                "debt_before_day_end": debt_before_day_end,
                "can_pay_off_before_day_end": balance_before_day_end >= debt_before_day_end,
                "repaid_today": debt_before_day_end - engine.state.debt_remaining,
            })
            if overlay is not None:
                repayment = overlay.repay_after_day_end(engine, strategy, repayment_behavior)
                economic_trace.append({"day": engine.state.day, **repayment})
                overlay.capture_deadline(engine.state.day)
            daily.append(day_snapshot(engine, final_satisfaction, review_stars, overlay))
            if engine.state.day == days:
                break
            next_day = engine.start_next_day()
            if not next_day.get("success"):
                raise RuntimeError(f"start next day rejected: {next_day}")
        first_rating_days = {
            str(threshold): next(
                (row["day"] for row in daily if row["average_rating"] is not None and row["average_rating"] >= threshold),
                None,
            ) for threshold in (3.5, 4.0)
        }
        pressure = {
            "food_shortage_events": sum(row["food_shortage"] for row in daily),
            "food_shortage_groups": sum(row["food_shortage_groups"] for row in daily),
            "turned_away_day": sum(row["turned_away_day"] for row in daily),
            "turned_away_overnight": sum(row["turned_away_overnight"] for row in daily),
            "turned_away_no_suitable_tent": sum(row["turned_away_no_suitable_tent"] for row in daily),
            "tent_failures": tent_failures, "tent_repairs": tent_repairs,
            "day_campsite_full_days": sum(row["daytime_served"] >= engine.DAY_CAMPSITE_CAPACITY for row in daily),
        }
        completed_all = all(day is not None for day in purchases.values())
        last_purchase_day = max((day for day in purchases.values() if day is not None), default=None)
        debt_checkpoints = {
            day: next((row["debt_remaining"] for row in daily if row["day"] == day), None)
            for day in (17, 20, 25, 30)
        }
        debt_summary = None
        if overlay is not None:
            debt_summary = {
                "initial_debt": overlay.initial_debt,
                "repayment_deadline_day": overlay.repayment_deadline_day,
                "first_repayment_day": overlay.first_repayment_day,
                "debt_paid_off_day": overlay.debt_paid_off_day,
                "debt_remaining_at_day17": debt_checkpoints[17],
                "debt_remaining_at_day20": debt_checkpoints[20],
                "debt_remaining_at_day25": debt_checkpoints[25],
                "debt_remaining_at_day30": debt_checkpoints[30],
                "debt_remaining_at_deadline": next(
                    (row["debt_remaining"] for row in daily if row["day"] == overlay.repayment_deadline_day),
                    None,
                ),
                "deadline_paid_off": overlay.overdue_amount_at_deadline == 0,
                "overdue_amount_at_deadline": overlay.overdue_amount_at_deadline,
                "cumulative_lodging_consumable_cost": overlay.cumulative_lodging_consumable_cost,
                "cumulative_hot_spring_operating_cost": overlay.cumulative_hot_spring_operating_cost,
                "cumulative_debt_repayment": overlay.cumulative_debt_repayment,
            }
        milestones = {
            threshold: next(
                (row["day"] for row in daily if row["cumulative_service_groups"] >= threshold),
                None,
            )
            for threshold in (50, 100, 150)
        }
        return {"seed": seed, "strategy": strategy, "daily": daily, "purchases": purchases,
                "qualification_days": qualification_days,
                "formal_repayment_trace": formal_repayment_trace,
                "milestones": milestones,
                "hot_spring_eligible_day": hot_spring_eligible_day,
                "first_rating_days": first_rating_days, "pressure": pressure,
                "completed_all_growth": completed_all, "last_purchase_day": last_purchase_day,
                "economic_config": dict(economic_config) if economic_config is not None else None,
                "repayment_behavior": repayment_behavior,
                "repayment_unlock_day": repayment_unlock_day,
                "review_five_star_threshold": review_five_star_threshold,
                "review_events": review_events,
                "telemetry": telemetry if capture_telemetry else None,
                "debt": debt_summary, "economic_trace": economic_trace}
    finally:
        # Windows sandbox may retain a transient file handle. It must not change
        # simulation results or cause a successful run to be reported as failed.
        shutil.rmtree(temp_dir, ignore_errors=True)


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        for row in run["daily"]:
            by_day[row["day"]].append(row)
    metrics = ("gold", "average_rating", "daily_income", "daily_expense", "daily_net", "daytime_natural_demand", "overnight_natural_demand", "cumulative_service_groups", "debt_remaining")
    daily_summary = {}
    for day, rows in sorted(by_day.items()):
        daily_summary[day] = {metric: quantiles([float(r[metric]) for r in rows if r[metric] is not None]) for metric in metrics}
    purchase_summary = {}
    for project in PROJECT_IDS:
        values = [float(run["purchases"].get(project)) for run in runs if run["purchases"].get(project) is not None]
        purchase_summary[project] = quantiles(values)
    checkpoints = {}
    for day in CHECKPOINT_DAYS:
        if day not in by_day:
            continue
        rows = by_day[day]
        stars = {f"stars_{n}": sum(r[f"stars_{n}"] for r in rows) for n in range(1, 6)}
        total = sum(stars.values())
        checkpoints[day] = {
            "gold": quantiles([float(r["gold"]) for r in rows]),
            "star_proportions": {str(n): (stars[f"stars_{n}"] / total if total else 0.0) for n in range(1, 6)},
            "rating": quantiles([float(r["average_rating"]) for r in rows if r["average_rating"] is not None]),
            "state_completion": {
                "tent_2": sum(r["unlocked_tent_count"] >= 2 for r in rows) / len(rows),
                "tent_3": sum(r["unlocked_tent_count"] >= 3 for r in rows) / len(rows),
                "tent_4": sum(r["unlocked_tent_count"] >= 4 for r in rows) / len(rows),
                "tent_5": sum(r["unlocked_tent_count"] >= 5 for r in rows) / len(rows),
                "tent_6": sum(r["unlocked_tent_count"] >= 6 for r in rows) / len(rows),
                "dining_lv1": sum(r["dining_level"] >= 1 for r in rows) / len(rows),
                "dining_lv2": sum(r["dining_level"] >= 2 for r in rows) / len(rows),
                "entertainment_lv1": sum(r["entertainment_level"] >= 1 for r in rows) / len(rows),
                "entertainment_lv2": sum(r["entertainment_level"] >= 2 for r in rows) / len(rows),
                "greenery_lv1": sum(r["greenery_level"] >= 1 for r in rows) / len(rows),
                "greenery_lv2": sum(r["greenery_level"] >= 2 for r in rows) / len(rows),
                "hot_spring": sum(bool(r["hot_spring_built"]) for r in rows) / len(rows),
            },
        }
    hot_values = [float(run["purchases"].get("hot_spring")) for run in runs if run["purchases"].get("hot_spring") is not None]
    rating_thresholds = {}
    for threshold in ("3.5", "4.0"):
        values = [float(run["first_rating_days"][threshold]) for run in runs if run["first_rating_days"][threshold] is not None]
        rating_thresholds[threshold] = {
            "first_day": quantiles(values),
            "not_reached_ratio": 1 - len(values) / len(runs),
        }
    pressure_keys = next(iter(runs))["pressure"].keys()
    pressure_summary = {key: quantiles([float(run["pressure"][key]) for run in runs]) for key in pressure_keys}
    completion = {
        "all_growth_completed_ratio": sum(run["completed_all_growth"] for run in runs) / len(runs),
        "last_purchase_day": quantiles([float(run["last_purchase_day"]) for run in runs if run["last_purchase_day"] is not None]),
    }
    debt_runs = [run["debt"] for run in runs if run.get("debt") is not None]
    debt_summary = None
    if debt_runs:
        debt_summary = {
            "initial_debt": debt_runs[0]["initial_debt"],
            "repayment_deadline_day": debt_runs[0]["repayment_deadline_day"],
            "deadline_paid_off_ratio": sum(item["deadline_paid_off"] for item in debt_runs) / len(debt_runs),
            "first_repayment_day": quantiles([float(item["first_repayment_day"]) for item in debt_runs if item["first_repayment_day"] is not None]),
            "debt_paid_off_day": quantiles([float(item["debt_paid_off_day"]) for item in debt_runs if item["debt_paid_off_day"] is not None]),
            "debt_remaining_at_day17": quantiles([float(item["debt_remaining_at_day17"]) for item in debt_runs if item["debt_remaining_at_day17"] is not None]),
            "debt_remaining_at_day20": quantiles([float(item["debt_remaining_at_day20"]) for item in debt_runs if item["debt_remaining_at_day20"] is not None]),
            "debt_remaining_at_day25": quantiles([float(item["debt_remaining_at_day25"]) for item in debt_runs if item["debt_remaining_at_day25"] is not None]),
            "debt_remaining_at_deadline": quantiles([float(item["debt_remaining_at_deadline"]) for item in debt_runs if item["debt_remaining_at_deadline"] is not None]),
            "debt_remaining_at_day30": quantiles([float(item["debt_remaining_at_day30"]) for item in debt_runs if item["debt_remaining_at_day30"] is not None]),
            "overdue_amount_at_deadline": quantiles([float(item["overdue_amount_at_deadline"]) for item in debt_runs if item["overdue_amount_at_deadline"] is not None]),
            "cumulative_lodging_consumable_cost": quantiles([float(item["cumulative_lodging_consumable_cost"]) for item in debt_runs]),
            "cumulative_hot_spring_operating_cost": quantiles([float(item["cumulative_hot_spring_operating_cost"]) for item in debt_runs]),
            "cumulative_debt_repayment": quantiles([float(item["cumulative_debt_repayment"]) for item in debt_runs]),
        }
    return {"daily": daily_summary, "purchases": purchase_summary, "checkpoints": checkpoints,
            "hot_spring_purchase_day": quantiles(hot_values), "rating_thresholds": rating_thresholds,
            "pressure": pressure_summary, "completion": completion, "debt": debt_summary}


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        rows = []
        blocks = []
        blocks = list(payload["strategies"].items())
        for strategy, block in blocks:
            for run in block["runs"]:
                for row in run["daily"]:
                    rows.append({"strategy": strategy, "seed": run["seed"], **{k: v for k, v in row.items() if k != "satisfaction_distribution"}})
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["strategy"])
            writer.writeheader(); writer.writerows(rows)
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Camping Plaza Balance Simulator v1")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--strategy", choices=STRATEGIES, action="append", help="omit to run all strategies")
    parser.add_argument("--repayment-behavior", choices=REPAYMENT_BEHAVIORS, default="deadline_aware")
    parser.add_argument("--repayment-unlock-day", type=int, default=1)
    parser.add_argument("--review-five-star-threshold", type=int, default=84)
    parser.add_argument("--economic-scenario", choices=tuple(REPRESENTATIVE_ECONOMIC_SCENARIOS), help="run representative debt/cost overlay scenario; D is the no-overlay control")
    parser.add_argument("--output", type=Path, help="write JSON, or CSV when suffix is .csv")
    args = parser.parse_args()
    if args.days < 1 or args.runs < 1 or args.repayment_unlock_day < 1:
        parser.error("--days, --runs, and --repayment-unlock-day must be positive")
    if not 72 <= args.review_five_star_threshold <= 100:
        parser.error("--review-five-star-threshold must be between 72 and 100")
    selected = args.strategy or list(STRATEGIES)
    economic_config = REPRESENTATIVE_ECONOMIC_SCENARIOS.get(args.economic_scenario) if args.economic_scenario else None
    def run_strategy(index: int, strategy: str) -> dict[str, Any]:
        runs = [simulate_run(args.days, args.seed + index * 1_000_000 + run_index, strategy, economic_config, args.repayment_behavior, args.repayment_unlock_day, args.review_five_star_threshold) for run_index in range(args.runs)]
        return {"runs": runs, "summary": summarize_runs(runs)}

    payload = {"days": args.days, "runs": args.runs, "seed": args.seed, "real_day17": REAL_DAY17, "economic_scenario": args.economic_scenario, "economic_config": economic_config, "repayment_behavior": args.repayment_behavior, "repayment_unlock_day": args.repayment_unlock_day, "review_five_star_threshold": args.review_five_star_threshold, "strategies": {}}
    for index, strategy in enumerate(selected):
        payload["strategies"][strategy] = run_strategy(index, strategy)
    print(f"Balance Simulator v1 | days={args.days} runs={args.runs} seed={args.seed} economic={args.economic_scenario or 'off'}")
    if 17 <= args.days:
        print("Day 17 benchmark: gold=5050 rating=3.2826 reviews=46 served=109 tents=1-4 dining=1 entertainment=2 greenery=2")
    for strategy, block in payload["strategies"].items():
        if 17 <= args.days:
            summary = block["summary"]["daily"][17]
            print(f"{strategy}: gold {summary['gold']} | rating {summary['average_rating']} | served {summary['cumulative_service_groups']}")
        end = block["summary"]["daily"][args.days]
        print(f"{strategy} Day {args.days}: gold={end['gold']} rating={end['average_rating']} net={end['daily_net']} day_demand={end['daytime_natural_demand']} overnight_demand={end['overnight_natural_demand']}")
    if args.output:
        write_report(args.output, payload)
        print(f"report written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
