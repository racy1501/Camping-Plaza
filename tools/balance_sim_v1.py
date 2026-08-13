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
CHECKPOINT_DAYS = (15, 17, 20, 25, 30)
RATING_SCENARIOS = {
    "baseline": (74, 88),
    "historical_control": (75, 90),
    "combined_stronger": (73, 87),
}
FORMAL_RATING_THRESHOLDS = (74, 88)
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


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "median": None, "p90": None}
    ordered = sorted(values)
    def pick(p: float) -> float:
        return ordered[round((len(ordered) - 1) * p)]
    return {"p10": pick(0.10), "median": pick(0.50), "p90": pick(0.90)}


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


def day_end_actions(engine: CampingPlazaEngine, strategy: str) -> list[dict]:
    """用正式 catalog 判断可购项目，并确定三种策略的项目/维护顺序。"""
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
    return actions


def day_snapshot(
    engine: CampingPlazaEngine, satisfaction: Counter[int], review_stars: Counter[int]
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
    return {
        "day": state.day, "gold": state.balance,
        "average_rating": engine.get_average_rating(), "review_count": state.total_reviews,
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
        "daily_income": income, "daily_expense": expense, "daily_net": income - expense,
        "cumulative_service_groups": state.total_served_groups,
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


def rating_function(four_star_threshold: int, five_star_threshold: int):
    def calculate_rating(satisfaction: float) -> int:
        if satisfaction >= five_star_threshold:
            return 5
        if satisfaction >= four_star_threshold:
            return 4
        if satisfaction >= 60:
            return 3
        if satisfaction >= 45:
            return 2
        return 1
    return calculate_rating


def simulate_run(
    days: int, seed: int, strategy: str,
    rating_thresholds: tuple[int, int] = FORMAL_RATING_THRESHOLDS,
) -> dict[str, Any]:
    random.seed(seed)
    SIM_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="run-", dir=SIM_TEMP_ROOT)
    engine = None
    original_rating = None
    try:
        engine = CampingPlazaEngine(str(Path(temp_dir) / "run.sqlite"))
        original_rating = engine._calculate_rating
        engine._calculate_rating = rating_function(*rating_thresholds)  # type: ignore[method-assign]
        final_satisfaction: Counter[int] = Counter()
        review_stars: Counter[int] = Counter()
        tent_failures = 0
        original_review = engine._try_leave_review
        def capture_review(npc: Any, result: dict) -> None:
            final_satisfaction[int(round(npc.total_satisfaction))] += 1
            original_review(npc, result)
            if npc.review_left:
                review_stars[int(npc.review_rating)] += 1
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
        daily: list[dict[str, Any]] = []
        purchases: dict[str, int | None] = {project: None for project in PROJECT_IDS}
        hot_spring_eligible_day: int | None = None
        tent_repairs = 0
        while engine.state.day <= days:
            while engine.state.turn <= 5:
                event = engine.get_current_temporary_conflict_event()
                if event is not None:
                    engine.resolve_current_temporary_conflict("mediate")
                if engine.state.turn in (2, 3, 4, 5):
                    free, actions = turn_actions(engine, strategy)
                    submitted = engine.submit_turn_plan(free, actions)
                    if not submitted.get("success"):
                        raise RuntimeError(f"turn plan rejected: {submitted}")
                engine.advance_turn()
            catalog_before = engine.get_growth_project_catalog()
            hot = next(x for x in catalog_before if x["project_id"] == "hot_spring")
            if hot_spring_eligible_day is None and hot["prerequisite_met"] and hot["operation_requirement_met"]:
                hot_spring_eligible_day = engine.state.day
            result = engine.submit_day_end_actions(day_end_actions(engine, strategy))
            if not result.get("success"):
                raise RuntimeError(f"day end rejected: {result}")
            for item in result.get("results", []):
                if item.get("success") and item.get("action") == "repair_tent":
                    tent_repairs += 1
                if item.get("success") and item.get("action") == "purchase_growth_project":
                    project = (item.get("params") or {}).get("project_id")
                    if project in purchases and purchases[project] is None:
                        purchases[project] = engine.state.day
            daily.append(day_snapshot(engine, final_satisfaction, review_stars))
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
        return {"seed": seed, "strategy": strategy, "daily": daily, "purchases": purchases,
                "hot_spring_eligible_day": hot_spring_eligible_day,
                "first_rating_days": first_rating_days, "pressure": pressure,
                "completed_all_growth": completed_all, "last_purchase_day": last_purchase_day}
    finally:
        if engine is not None and original_rating is not None:
            engine._calculate_rating = original_rating  # type: ignore[method-assign]
        # Windows sandbox may retain a transient file handle. It must not change
        # simulation results or cause a successful run to be reported as failed.
        shutil.rmtree(temp_dir, ignore_errors=True)


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        for row in run["daily"]:
            by_day[row["day"]].append(row)
    metrics = ("gold", "average_rating", "daily_income", "daily_expense", "daily_net", "daytime_natural_demand", "overnight_natural_demand", "cumulative_service_groups")
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
    return {"daily": daily_summary, "purchases": purchase_summary, "checkpoints": checkpoints,
            "hot_spring_purchase_day": quantiles(hot_values), "rating_thresholds": rating_thresholds,
            "pressure": pressure_summary, "completion": completion}


def scenario_delta(summary: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    day = 30
    current = summary["daily"].get(day, {})
    reference = baseline["daily"].get(day, {})
    def delta(metric: str) -> float | None:
        a = current.get(metric, {}).get("median")
        b = reference.get(metric, {}).get("median")
        return None if a is None or b is None else a - b
    current_stars = summary["checkpoints"].get(day, {}).get("star_proportions", {})
    base_stars = baseline["checkpoints"].get(day, {}).get("star_proportions", {})
    return {
        "average_rating": delta("average_rating"),
        "five_star_proportion": current_stars.get("5", 0) - base_stars.get("5", 0),
        "daytime_demand": delta("daytime_natural_demand"),
        "overnight_demand": delta("overnight_natural_demand"),
        "gold": delta("gold"),
        "cumulative_service_groups": delta("cumulative_service_groups"),
        "last_growth_purchase_day": (summary["completion"]["last_purchase_day"].get("median") or 0)
            - (baseline["completion"]["last_purchase_day"].get("median") or 0),
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        rows = []
        blocks = []
        if payload.get("mode") == "rating_sensitivity":
            for scenario, scenario_block in payload["scenarios"].items():
                for strategy, block in scenario_block["strategies"].items():
                    blocks.append((f"{scenario}:{strategy}", block))
        else:
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
    parser.add_argument("--rating-sensitivity", action="store_true", help="run the five rating-threshold scenarios")
    parser.add_argument("--output", type=Path, help="write JSON, or CSV when suffix is .csv")
    args = parser.parse_args()
    if args.days < 1 or args.runs < 1:
        parser.error("--days and --runs must be positive")
    selected = args.strategy or list(STRATEGIES)
    def run_strategy(index: int, strategy: str, thresholds: tuple[int, int]) -> dict[str, Any]:
        runs = [simulate_run(args.days, args.seed + index * 1_000_000 + run_index, strategy, thresholds) for run_index in range(args.runs)]
        return {"runs": runs, "summary": summarize_runs(runs)}

    if args.rating_sensitivity:
        payload = {"mode": "rating_sensitivity", "days": args.days, "runs": args.runs, "seed": args.seed, "real_day17": REAL_DAY17, "scenarios": {}}
        for scenario, thresholds in RATING_SCENARIOS.items():
            strategies = {strategy: run_strategy(index, strategy, thresholds) for index, strategy in enumerate(selected)}
            payload["scenarios"][scenario] = {"thresholds": thresholds, "strategies": strategies}
        baseline = payload["scenarios"]["baseline"]["strategies"]
        for scenario_block in payload["scenarios"].values():
            for strategy, block in scenario_block["strategies"].items():
                block["delta_vs_baseline_day30"] = scenario_delta(block["summary"], baseline[strategy]["summary"])
    else:
        payload = {"days": args.days, "runs": args.runs, "seed": args.seed, "real_day17": REAL_DAY17, "strategies": {}}
        for index, strategy in enumerate(selected):
            payload["strategies"][strategy] = run_strategy(index, strategy, FORMAL_RATING_THRESHOLDS)
    print(f"Balance Simulator v1 | days={args.days} runs={args.runs} seed={args.seed}")
    if 17 <= args.days:
        print("Day 17 benchmark: gold=5050 rating=3.2826 reviews=46 served=109 tents=1-4 dining=1 entertainment=2 greenery=2")
    if args.rating_sensitivity:
        for scenario, scenario_block in payload["scenarios"].items():
            print(f"[{scenario}] thresholds={scenario_block['thresholds']}")
            for strategy, block in scenario_block["strategies"].items():
                end = block["summary"]["daily"][args.days]
                print(f"{strategy} Day {args.days}: rating={end['average_rating']} 5star={block['summary']['checkpoints'].get(args.days, {}).get('star_proportions', {}).get('5', 0):.3f} gold={end['gold']} delta={block['delta_vs_baseline_day30']}")
    else:
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
