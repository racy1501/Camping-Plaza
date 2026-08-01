from __future__ import annotations

import argparse
import random
import shutil
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from economy_baseline_sim import (  # type: ignore
    clean_all_tents,
    count_fault_events,
    create_engine,
    fmt_int,
    fmt_num,
    fmt_pct,
    md_table,
    repair_all_broken_tents,
    stats_row,
    submit_minimal_plan,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "docs" / "analysis" / "tent2_unlock_sensitivity_v1.md"

DEFAULT_SEEDS = 200
DEFAULT_DAYS = 20
UNLOCK_BUFFER = 130
PRICE_GRID = [800, 850, 875]
UNLOCK_CHECKPOINTS = (1, 2, 3, 4, 5, 7, 10)
BALANCE_CHECKPOINTS = (5, 10, 15, 20)
THRESHOLDS = (50, 80, 130)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run tent-2 unlock sensitivity analysis against the live GameEngine."
    )
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    return parser


def record_min_balance(summary: dict[str, Any], balance: float) -> None:
    summary["min_balance_seen"] = min(summary["min_balance_seen"], float(balance))


def maybe_unlock_tent2(engine: Any, unlock_price: int) -> bool:
    if engine.state.day < 2 or engine.state.turn != 6:
        return False
    tent = engine.tents[2]
    if tent.is_unlocked:
        return False
    if engine.state.balance - unlock_price < UNLOCK_BUFFER:
        return False

    engine.state.balance -= unlock_price
    tent.is_unlocked = True
    engine._set_next_breakdown(tent)
    return True


def reservation_income_for_tent2(engine: Any, profile: dict[str, Any]) -> float:
    if profile.get("reservation_result") != "accepted_overnight":
        return 0.0
    reservation = engine.state.reservation or {}
    if reservation.get("tent_id") != 2:
        return 0.0
    return float(engine.TENT_PRICES[2])


def count_tent2_checkins(
    engine: Any,
    processed_turn: int,
    seen_npc_ids: set[int],
) -> tuple[int, float]:
    checkins = 0
    income = 0.0
    price = float(engine.TENT_PRICES[2])

    for npc in engine.npc_pool:
        if npc.id in seen_npc_ids:
            continue
        if npc.location != "tent_2" or npc.arrival_turn != processed_turn:
            continue
        seen_npc_ids.add(npc.id)
        checkins += 1
        if not npc.is_reserved:
            income += price

    return checkins, income


def count_new_dining_failures(
    engine: Any,
    seen_failures: set[tuple[Any, ...]],
) -> int:
    failures = 0
    for entry in engine.state.today_arrival_plan:
        if entry.get("planned_day") != engine.state.day:
            continue
        for action in entry.get("planned_actions", []):
            if action.get("action") != "dining":
                continue
            if action.get("status") != "failed":
                continue
            if action.get("result") != "insufficient_food":
                continue
            key = (
                entry.get("npc_id"),
                entry.get("planned_day"),
                action.get("planned_turn"),
                action.get("menu_key"),
            )
            if key in seen_failures:
                continue
            seen_failures.add(key)
            failures += 1
    return failures


def find_npc_by_id(engine: Any, npc_id: int) -> Any | None:
    for npc in engine.npc_pool:
        if npc.id == npc_id:
            return npc
    return None


def collect_tent2_activity(
    engine: Any,
    current_day: int,
    processed_turn: int,
    seen_tent2_ids: set[int],
    seen_natural_overnight_keys: set[tuple[Any, ...]],
    summary: dict[str, Any],
) -> None:
    tent2_price = float(engine.TENT_PRICES[2])

    for entry in engine.state.today_arrival_plan:
        if entry.get("planned_day") != current_day:
            continue
        if entry.get("arrival_turn") != processed_turn:
            continue
        if entry.get("source") != "natural_overnight":
            continue
        key = (
            entry.get("npc_id"),
            entry.get("planned_day"),
            entry.get("arrival_turn"),
            entry.get("source"),
        )
        if key in seen_natural_overnight_keys:
            continue
        seen_natural_overnight_keys.add(key)
        if entry.get("arrival_status") == "arrived":
            summary["natural_overnight_served_total"] += 1
        elif entry.get("arrival_status") == "turned_away_full":
            summary["natural_overnight_lost_total"] += 1

    for npc in engine.npc_pool:
        if npc.id in seen_tent2_ids:
            continue
        if npc.location != "tent_2" or npc.arrival_turn != processed_turn:
            continue
        seen_tent2_ids.add(npc.id)
        plan_entry = None
        for entry in engine.state.today_arrival_plan:
            if entry.get("planned_day") == current_day and entry.get("npc_id") == npc.id:
                plan_entry = entry
                break

        if plan_entry is not None and plan_entry.get("source") == "reservation":
            summary["tent2_source_counts"]["overnight_reservation"] += 1
            summary["tent2_source_income"]["overnight_reservation"] += 0.0
        elif plan_entry is not None and plan_entry.get("source") == "natural_overnight":
            summary["tent2_source_counts"]["natural_overnight"] += 1
            summary["tent2_source_income"]["natural_overnight"] += tent2_price
        elif processed_turn == 4:
            summary["tent2_source_counts"]["day_to_overnight"] += 1
            summary["tent2_source_income"]["day_to_overnight"] += tent2_price
        else:
            summary["tent2_source_counts"]["other"] += 1
            summary["tent2_source_income"]["other"] += tent2_price

        summary["tent2_checkins_total"] += 1
        if not (plan_entry is not None and plan_entry.get("source") == "reservation"):
            summary["tent2_income_total"] += tent2_price


def end_of_day_management(engine: Any, summary: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {
        "repair_count": 0,
        "food_spend": 0.0,
        "greenery_spend": 0.0,
        "food_purchase_success": False,
        "food_purchase_failed_insufficient_balance": False,
        "greenery_result": "",
    }

    info["repair_count"] += repair_all_broken_tents(engine)
    record_min_balance(summary, engine.state.balance)

    if not clean_all_tents(engine):
        raise RuntimeError("clean_tents failed during sensitivity management")
    record_min_balance(summary, engine.state.balance)

    before_greenery = engine.state.balance
    greenery_result = engine.manage_greenery("maintain")
    info["greenery_result"] = greenery_result
    info["greenery_spend"] = before_greenery - engine.state.balance
    record_min_balance(summary, before_greenery)
    record_min_balance(summary, engine.state.balance)

    if engine.state.food_stock < 4:
        before_food = engine.state.balance
        food_result = engine.buy_food_package("small")
        if food_result.get("success"):
            info["food_purchase_success"] = True
            info["food_spend"] = before_food - engine.state.balance
        else:
            if "余额不足" in str(food_result.get("message", "")):
                info["food_purchase_failed_insufficient_balance"] = True
        record_min_balance(summary, before_food)
        record_min_balance(summary, engine.state.balance)

    return info


def pre_post_cash_flow_means(
    daily_cash_flow: list[float],
    unlock_day: int | None,
) -> tuple[float | None, float | None]:
    if not daily_cash_flow:
        return None, None
    if unlock_day is None:
        return statistics.mean(daily_cash_flow), None
    pre_slice = daily_cash_flow[:unlock_day]
    post_slice = daily_cash_flow[unlock_day:]
    return (
        statistics.mean(pre_slice) if pre_slice else None,
        statistics.mean(post_slice) if post_slice else None,
    )


def run_single_seed(seed: int, days: int, unlock_price: int | None) -> dict[str, Any]:
    engine, temp_dir = create_engine(seed)
    try:
        summary: dict[str, Any] = {
            "seed": seed,
            "unlock_price": unlock_price,
            "blocked": False,
            "block_reason": "",
            "completed_days": 0,
            "start_balance": float(engine.state.balance),
            "final_balance": 0.0,
            "balance_delta": 0.0,
            "total_income": 0.0,
            "total_expenses": 0.0,
            "ledger_delta": 0.0,
            "ledger_gap": 0.0,
            "negative_balance": False,
            "faults": 0,
            "min_balance_seen": float(engine.state.balance),
            "daily_cash_flow": [],
            "daily_end_balance": [],
            "unlock_day": None,
            "first_usable_day": None,
            "unlock_cost": 0.0,
            "unlock_success": False,
            "food_purchase_failed_total": 0,
            "food_purchase_failed_days": [],
            "first_food_purchase_failed_day": None,
            "food_purchase_failed_within_7d_after_unlock": False,
            "dining_insufficient_food_total": 0,
            "reservation_request_total": 0,
            "reservation_overnight_request_total": 0,
            "reservation_day_request_total": 0,
            "reservation_accepted_day_total": 0,
            "reservation_accepted_overnight_total": 0,
            "reservation_rejected_capacity_total": 0,
            "reservation_skipped_existing_total": 0,
            "natural_overnight_demand_total": 0,
            "natural_overnight_served_total": 0,
            "natural_overnight_lost_total": 0,
            "tent2_source_counts": {
                "natural_overnight": 0,
                "overnight_reservation": 0,
                "day_to_overnight": 0,
                "other": 0,
            },
            "tent2_source_income": {
                "natural_overnight": 0.0,
                "overnight_reservation": 0.0,
                "day_to_overnight": 0.0,
                "other": 0.0,
            },
            "tent2_checkins_total": 0,
            "tent2_income_total": 0.0,
            "pre_unlock_cash_flow_mean": None,
            "post_unlock_cash_flow_mean": None,
            "daily_balance_checkpoint": {},
        }
        seen_tent2_ids: set[int] = set()
        seen_natural_overnight_keys: set[tuple[Any, ...]] = set()
        seen_dining_failures: set[tuple[Any, ...]] = set()

        while summary["completed_days"] < days:
            current_day = engine.state.day
            profile = dict(engine.state.daily_demand_profile or {})
            record_min_balance(summary, engine.state.balance)

            summary["reservation_request_total"] += int(profile.get("reservation_request_available", False))
            summary["reservation_overnight_request_total"] += int(
                bool(profile.get("reservation_request_available"))
                and profile.get("reservation_visit_type") == "overnight"
            )
            summary["reservation_day_request_total"] += int(
                bool(profile.get("reservation_request_available"))
                and profile.get("reservation_visit_type") == "day"
            )
            summary["reservation_accepted_day_total"] += int(profile.get("reservation_result") == "accepted_day")
            summary["reservation_accepted_overnight_total"] += int(
                profile.get("reservation_result") == "accepted_overnight"
            )
            summary["reservation_rejected_capacity_total"] += int(
                profile.get("reservation_result") == "rejected_overnight_capacity"
            )
            summary["reservation_skipped_existing_total"] += int(
                profile.get("reservation_result") == "skipped_existing_reservation"
            )

            summary["natural_overnight_demand_total"] += int(
                profile.get("natural_overnight_group_demand", 0)
            )

            tent2_income_today = reservation_income_for_tent2(engine, profile)
            if tent2_income_today > 0:
                summary["tent2_source_income"]["overnight_reservation"] += tent2_income_today
                summary["tent2_income_total"] += tent2_income_today

            while engine.state.day == current_day and engine.state.turn < 6:
                if engine.state.turn in (2, 3, 4, 5):
                    plan_result = submit_minimal_plan(engine)
                    if not plan_result.get("success"):
                        raise RuntimeError(
                            f"turn plan rejected on seed {seed}, day {current_day}, "
                            f"turn {engine.state.turn}: {plan_result.get('message')}"
                        )

                processed_turn = engine.state.turn
                advance_result = engine.advance_turn()
                summary["faults"] += count_fault_events(advance_result)
                record_min_balance(summary, engine.state.balance)
                summary["dining_insufficient_food_total"] += count_new_dining_failures(
                    engine, seen_dining_failures
                )
                collect_tent2_activity(
                    engine,
                    current_day,
                    processed_turn,
                    seen_tent2_ids,
                    seen_natural_overnight_keys,
                    summary,
                )

                if engine.state.day != current_day and engine.state.turn != 1:
                    raise RuntimeError(
                        f"unexpected turn jump on seed {seed}, day {current_day}"
                    )

            management_result = end_of_day_management(engine, summary)
            management_spend = float(management_result["food_spend"]) + float(
                management_result["greenery_spend"]
            )

            if management_result.get("food_purchase_failed_insufficient_balance"):
                summary["food_purchase_failed_total"] += 1
                summary["food_purchase_failed_days"].append(current_day)
                if summary["first_food_purchase_failed_day"] is None:
                    summary["first_food_purchase_failed_day"] = current_day

            unlock_spend = 0.0
            if unlock_price is not None:
                if maybe_unlock_tent2(engine, unlock_price):
                    summary["unlock_success"] = True
                    summary["unlock_day"] = current_day
                    summary["first_usable_day"] = current_day + 1
                    summary["unlock_cost"] += float(unlock_price)
                    unlock_spend = float(unlock_price)
                    record_min_balance(summary, engine.state.balance)

            if (
                summary["first_food_purchase_failed_day"] is not None
                and summary["unlock_day"] is not None
                and current_day > summary["unlock_day"]
                and current_day <= summary["unlock_day"] + 6
            ):
                summary["food_purchase_failed_within_7d_after_unlock"] = True

            day_income = float(sum(engine.state.today_income.values()))
            day_expenses = management_spend + unlock_spend

            summary["total_income"] += day_income
            summary["total_expenses"] += day_expenses
            summary["daily_cash_flow"].append(day_income - day_expenses)
            summary["daily_end_balance"].append(float(engine.state.balance))
            summary["completed_days"] += 1
            if engine.state.balance < 0:
                summary["negative_balance"] = True

            if summary["completed_days"] >= days:
                break

            pre_day = engine.state.day
            pre_turn = engine.state.turn
            next_result = engine.advance_turn()
            summary["faults"] += count_fault_events(next_result)
            record_min_balance(summary, engine.state.balance)
            if engine.state.day != pre_day + 1 or engine.state.turn != 1:
                raise RuntimeError(
                    f"failed to enter next day on seed {seed}, day {pre_day}"
                )
            if pre_turn != 6:
                raise RuntimeError(
                    f"day transition attempted from non-management turn on seed {seed}"
                )

        summary["final_balance"] = float(engine.state.balance)
        summary["balance_delta"] = summary["final_balance"] - summary["start_balance"]
        summary["ledger_delta"] = summary["total_income"] - summary["total_expenses"]
        summary["ledger_gap"] = summary["ledger_delta"] - summary["balance_delta"]
        if abs(summary["ledger_gap"]) > 1e-9:
            raise RuntimeError(
                "ledger mismatch: income-expenses does not match balance change "
                f"(gap={summary['ledger_gap']})"
            )

        summary["pre_unlock_cash_flow_mean"], summary["post_unlock_cash_flow_mean"] = (
            pre_post_cash_flow_means(summary["daily_cash_flow"], summary["unlock_day"])
        )
        for checkpoint in BALANCE_CHECKPOINTS:
            if len(summary["daily_end_balance"]) >= checkpoint:
                summary["daily_balance_checkpoint"][checkpoint] = summary["daily_end_balance"][checkpoint - 1]

        return summary
    except Exception as exc:
        return {
            "seed": seed,
            "unlock_price": unlock_price,
            "blocked": True,
            "block_reason": f"{type(exc).__name__}: {exc}",
            "completed_days": summary.get("completed_days", 0) if "summary" in locals() else 0,
            "start_balance": summary.get("start_balance", 0.0) if "summary" in locals() else 0.0,
            "final_balance": float(engine.state.balance) if "engine" in locals() else 0.0,
            "balance_delta": summary.get("balance_delta", 0.0) if "summary" in locals() else 0.0,
            "total_income": summary.get("total_income", 0.0) if "summary" in locals() else 0.0,
            "total_expenses": summary.get("total_expenses", 0.0) if "summary" in locals() else 0.0,
            "ledger_delta": summary.get("ledger_delta", 0.0) if "summary" in locals() else 0.0,
            "ledger_gap": summary.get("ledger_gap", 0.0) if "summary" in locals() else 0.0,
            "negative_balance": summary.get("negative_balance", False) if "summary" in locals() else False,
            "faults": summary.get("faults", 0) if "summary" in locals() else 0,
            "min_balance_seen": summary.get("min_balance_seen", 0.0) if "summary" in locals() else 0.0,
            "daily_cash_flow": summary.get("daily_cash_flow", []) if "summary" in locals() else [],
            "daily_end_balance": summary.get("daily_end_balance", []) if "summary" in locals() else [],
            "unlock_day": summary.get("unlock_day") if "summary" in locals() else None,
            "unlock_cost": summary.get("unlock_cost", 0.0) if "summary" in locals() else 0.0,
            "unlock_success": summary.get("unlock_success", False) if "summary" in locals() else False,
            "food_purchase_failed_total": summary.get("food_purchase_failed_total", 0) if "summary" in locals() else 0,
            "food_purchase_failed_days": summary.get("food_purchase_failed_days", []) if "summary" in locals() else [],
            "first_food_purchase_failed_day": summary.get("first_food_purchase_failed_day") if "summary" in locals() else None,
            "food_purchase_failed_within_7d_after_unlock": summary.get("food_purchase_failed_within_7d_after_unlock", False) if "summary" in locals() else False,
            "dining_insufficient_food_total": summary.get("dining_insufficient_food_total", 0) if "summary" in locals() else 0,
            "reservation_request_total": summary.get("reservation_request_total", 0) if "summary" in locals() else 0,
            "reservation_overnight_request_total": summary.get("reservation_overnight_request_total", 0) if "summary" in locals() else 0,
            "reservation_day_request_total": summary.get("reservation_day_request_total", 0) if "summary" in locals() else 0,
            "reservation_accepted_day_total": summary.get("reservation_accepted_day_total", 0) if "summary" in locals() else 0,
            "reservation_accepted_overnight_total": summary.get("reservation_accepted_overnight_total", 0) if "summary" in locals() else 0,
            "reservation_rejected_capacity_total": summary.get("reservation_rejected_capacity_total", 0) if "summary" in locals() else 0,
            "reservation_skipped_existing_total": summary.get("reservation_skipped_existing_total", 0) if "summary" in locals() else 0,
            "natural_overnight_demand_total": summary.get("natural_overnight_demand_total", 0) if "summary" in locals() else 0,
            "natural_overnight_served_total": summary.get("natural_overnight_served_total", 0) if "summary" in locals() else 0,
            "natural_overnight_lost_total": summary.get("natural_overnight_lost_total", 0) if "summary" in locals() else 0,
            "tent2_checkins_total": summary.get("tent2_checkins_total", 0) if "summary" in locals() else 0,
            "tent2_income_total": summary.get("tent2_income_total", 0.0) if "summary" in locals() else 0.0,
            "pre_unlock_cash_flow_mean": summary.get("pre_unlock_cash_flow_mean") if "summary" in locals() else None,
            "post_unlock_cash_flow_mean": summary.get("post_unlock_cash_flow_mean") if "summary" in locals() else None,
            "daily_balance_checkpoint": summary.get("daily_balance_checkpoint", {}) if "summary" in locals() else {},
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def aggregate_scenario(
    label: str,
    unlock_price: int | None,
    scenario_runs: list[dict[str, Any]],
    baseline_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    completed = [run for run in scenario_runs if not run.get("blocked")]
    blocked = [run for run in scenario_runs if run.get("blocked")]

    unlock_day_values: list[float] = []
    first_usable_day_values: list[float] = []
    min_balance_values: list[float] = []
    food_failure_days: list[float] = []
    first_food_failure_days: list[float] = []
    pre_cash_flow_values: list[float] = []
    post_cash_flow_values: list[float] = []
    payback_days: list[float] = []
    tent2_source_counts_total = {
        "natural_overnight": 0,
        "overnight_reservation": 0,
        "day_to_overnight": 0,
        "other": 0,
    }
    tent2_source_income_total = {
        "natural_overnight": 0.0,
        "overnight_reservation": 0.0,
        "day_to_overnight": 0.0,
        "other": 0.0,
    }

    unlock_rates = {day: 0 for day in UNLOCK_CHECKPOINTS}
    threshold_below = {day: {threshold: 0 for threshold in THRESHOLDS} for day in BALANCE_CHECKPOINTS}
    end_balance_by_checkpoint = {day: [] for day in BALANCE_CHECKPOINTS}
    baseline_balance_by_checkpoint = {day: [] for day in BALANCE_CHECKPOINTS}
    diff_balance_by_checkpoint = {day: [] for day in BALANCE_CHECKPOINTS}

    food_failure_runs = 0
    food_failure_total = 0
    unlock_7d_food_failure_runs = 0
    unlock_success_runs = 0

    total_faults = 0
    negative_balance_runs = 0
    total_income = 0.0
    total_expenses = 0.0
    start_balance = 0.0
    balance_delta = 0.0
    ledger_delta = 0.0
    ledger_gap = 0.0
    natural_overnight_demand_total = 0
    natural_overnight_served_total = 0
    natural_overnight_lost_total = 0
    reservation_request_total = 0
    reservation_overnight_request_total = 0
    reservation_day_request_total = 0
    reservation_accepted_day_total = 0
    reservation_accepted_overnight_total = 0
    reservation_rejected_capacity_total = 0
    reservation_skipped_existing_total = 0
    dining_insufficient_food_total = 0
    tent2_checkins_total = 0
    tent2_income_total = 0.0
    overnight_served_delta_total = 0
    overnight_lost_delta_total = 0

    for index, run in enumerate(completed):
        total_faults += int(run.get("faults", 0))
        if run.get("negative_balance"):
            negative_balance_runs += 1
        total_income += float(run.get("total_income", 0.0))
        total_expenses += float(run.get("total_expenses", 0.0))
        start_balance += float(run.get("start_balance", 0.0))
        balance_delta += float(run.get("balance_delta", 0.0))
        ledger_delta += float(run.get("ledger_delta", 0.0))
        ledger_gap += float(run.get("ledger_gap", 0.0))
        natural_overnight_demand_total += int(run.get("natural_overnight_demand_total", 0))
        natural_overnight_served_total += int(run.get("natural_overnight_served_total", 0))
        natural_overnight_lost_total += int(run.get("natural_overnight_lost_total", 0))
        reservation_request_total += int(run.get("reservation_request_total", 0))
        reservation_overnight_request_total += int(run.get("reservation_overnight_request_total", 0))
        reservation_day_request_total += int(run.get("reservation_day_request_total", 0))
        reservation_accepted_day_total += int(run.get("reservation_accepted_day_total", 0))
        reservation_accepted_overnight_total += int(run.get("reservation_accepted_overnight_total", 0))
        reservation_rejected_capacity_total += int(run.get("reservation_rejected_capacity_total", 0))
        reservation_skipped_existing_total += int(run.get("reservation_skipped_existing_total", 0))
        dining_insufficient_food_total += int(run.get("dining_insufficient_food_total", 0))
        tent2_checkins_total += int(run.get("tent2_checkins_total", 0))
        tent2_income_total += float(run.get("tent2_income_total", 0.0))

        if run.get("food_purchase_failed_total", 0) > 0:
            food_failure_runs += 1
            food_failure_total += int(run["food_purchase_failed_total"])
            first_food_failure_day = run.get("first_food_purchase_failed_day")
            if first_food_failure_day is not None:
                first_food_failure_days.append(float(first_food_failure_day))
            for day in run.get("food_purchase_failed_days", []):
                food_failure_days.append(float(day))

        unlock_day = run.get("unlock_day")
        if unlock_day is not None:
            unlock_success_runs += 1
            unlock_day_values.append(float(unlock_day))
            first_usable_day = run.get("first_usable_day")
            if first_usable_day is not None:
                first_usable_day_values.append(float(first_usable_day))
            for day in UNLOCK_CHECKPOINTS:
                if int(unlock_day) <= day:
                    unlock_rates[day] += 1
            if run.get("food_purchase_failed_within_7d_after_unlock"):
                unlock_7d_food_failure_runs += 1

        pre_mean = run.get("pre_unlock_cash_flow_mean")
        post_mean = run.get("post_unlock_cash_flow_mean")
        if pre_mean is not None:
            pre_cash_flow_values.append(float(pre_mean))
        if post_mean is not None:
            post_cash_flow_values.append(float(post_mean))

        min_balance_values.append(float(run.get("min_balance_seen", 0.0)))

        for day in BALANCE_CHECKPOINTS:
            if len(run["daily_end_balance"]) >= day:
                value = float(run["daily_end_balance"][day - 1])
                end_balance_by_checkpoint[day].append(value)
                for threshold in THRESHOLDS:
                    if value < threshold:
                        threshold_below[day][threshold] += 1

        if baseline_runs is not None:
            base = baseline_runs[index]
            for day in BALANCE_CHECKPOINTS:
                if len(base["daily_end_balance"]) >= day and len(run["daily_end_balance"]) >= day:
                    scenario_value = float(run["daily_end_balance"][day - 1])
                    baseline_value = float(base["daily_end_balance"][day - 1])
                    baseline_balance_by_checkpoint[day].append(baseline_value)
                    diff_balance_by_checkpoint[day].append(scenario_value - baseline_value)

            if unlock_price is not None and base["daily_end_balance"]:
                payback_day = None
                for day_index in range(
                    1,
                    min(len(run["daily_end_balance"]), len(base["daily_end_balance"])) + 1,
                ):
                    diff = float(run["daily_end_balance"][day_index - 1]) - float(
                        base["daily_end_balance"][day_index - 1]
                    )
                    if diff >= unlock_price:
                        payback_day = day_index
                        break
                if payback_day is not None:
                    payback_days.append(float(payback_day))

        if baseline_runs is not None:
            base = baseline_runs[index]
            overnight_served_delta_total += int(run.get("natural_overnight_served_total", 0)) - int(
                base.get("natural_overnight_served_total", 0)
            )
            overnight_lost_delta_total += int(run.get("natural_overnight_lost_total", 0)) - int(
                base.get("natural_overnight_lost_total", 0)
            )

        source_counts = run.get("tent2_source_counts", {})
        source_income = run.get("tent2_source_income", {})
        for source in tent2_source_counts_total:
            tent2_source_counts_total[source] += int(source_counts.get(source, 0))
            tent2_source_income_total[source] += float(source_income.get(source, 0.0))

    completed_runs = len(completed)
    blocked_runs = len(blocked)
    total_runs = completed_runs + blocked_runs

    unlock_rate_rows = {
        day: (unlock_rates[day] / completed_runs if completed_runs else 0.0)
        for day in UNLOCK_CHECKPOINTS
    }
    unlock_day_stats = stats_row(unlock_day_values)
    first_usable_day_stats = stats_row(first_usable_day_values)
    first_food_failure_stats = stats_row(first_food_failure_days)
    food_failure_day_stats = stats_row(food_failure_days)
    min_balance_stats = stats_row(min_balance_values)
    pre_cash_flow_stats = stats_row(pre_cash_flow_values)
    post_cash_flow_stats = stats_row(post_cash_flow_values)
    payback_day_stats = stats_row(payback_days)

    end_balance_stats = {day: stats_row(values) for day, values in end_balance_by_checkpoint.items()}
    baseline_balance_stats = {day: stats_row(values) for day, values in baseline_balance_by_checkpoint.items()}
    diff_balance_stats = {day: stats_row(values) for day, values in diff_balance_by_checkpoint.items()}

    day_below_rates = {
        day: {threshold: (threshold_below[day][threshold] / completed_runs if completed_runs else 0.0) for threshold in THRESHOLDS}
        for day in BALANCE_CHECKPOINTS
    }

    return {
        "label": label,
        "unlock_price": unlock_price,
        "completed_runs": completed_runs,
        "blocked_runs": blocked_runs,
        "blocked_details": blocked,
        "total_runs": total_runs,
        "negative_balance_runs": negative_balance_runs,
        "total_faults": total_faults,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "start_balance_total": start_balance,
        "balance_delta_total": balance_delta,
        "ledger_delta_total": ledger_delta,
        "ledger_gap_total": ledger_gap,
        "natural_overnight_demand_total": natural_overnight_demand_total,
        "natural_overnight_served_total": natural_overnight_served_total,
        "natural_overnight_lost_total": natural_overnight_lost_total,
        "reservation_request_total": reservation_request_total,
        "reservation_overnight_request_total": reservation_overnight_request_total,
        "reservation_day_request_total": reservation_day_request_total,
        "reservation_accepted_day_total": reservation_accepted_day_total,
        "reservation_accepted_overnight_total": reservation_accepted_overnight_total,
        "reservation_rejected_capacity_total": reservation_rejected_capacity_total,
        "reservation_skipped_existing_total": reservation_skipped_existing_total,
        "dining_insufficient_food_total": dining_insufficient_food_total,
        "tent2_checkins_total": tent2_checkins_total,
        "tent2_income_total": tent2_income_total,
        "overnight_served_delta_total": overnight_served_delta_total,
        "overnight_lost_delta_total": overnight_lost_delta_total,
        "unlock_day_stats": unlock_day_stats,
        "first_usable_day_stats": first_usable_day_stats,
        "unlock_rate_rows": unlock_rate_rows,
        "unlock_day20_unlocked_rate": (len(unlock_day_values) / completed_runs if completed_runs else 0.0),
        "unlock_success_runs": unlock_success_runs,
        "food_failure_runs": food_failure_runs,
        "food_failure_total": food_failure_total,
        "food_failure_rate": (food_failure_runs / completed_runs if completed_runs else 0.0),
        "food_failure_total_mean": (food_failure_total / completed_runs if completed_runs else 0.0),
        "food_failure_day_stats": food_failure_day_stats,
        "first_food_failure_stats": first_food_failure_stats,
        "first_food_failure_days": first_food_failure_days,
        "food_failure_days": food_failure_days,
        "food_failure_within_7d_after_unlock_runs": unlock_7d_food_failure_runs,
        "food_failure_within_7d_after_unlock_rate": (
            unlock_7d_food_failure_runs / unlock_success_runs if unlock_success_runs else 0.0
        ),
        "min_balance_stats": min_balance_stats,
        "pre_cash_flow_stats": pre_cash_flow_stats,
        "post_cash_flow_stats": post_cash_flow_stats,
        "payback_day_stats": payback_day_stats,
        "payback_days": payback_days,
        "payback_rate_by_day": {
            day: (sum(1 for value in payback_days if value <= day) / completed_runs if completed_runs else 0.0)
            for day in (10, 15, 20)
        },
        "end_balance_stats": end_balance_stats,
        "baseline_balance_stats": baseline_balance_stats,
        "diff_balance_stats": diff_balance_stats,
        "day_below_rates": day_below_rates,
        "min_balance_values": min_balance_values,
        "pre_cash_flow_values": pre_cash_flow_values,
        "post_cash_flow_values": post_cash_flow_values,
        "tent2_source_counts_total": tent2_source_counts_total,
        "tent2_source_income_total": tent2_source_income_total,
    }


def classify_price(summary: dict[str, Any]) -> str:
    day1 = summary["unlock_rate_rows"][1]
    day2 = summary["unlock_rate_rows"][2]
    day3 = summary["unlock_rate_rows"][3]
    day4 = summary["unlock_rate_rows"][4]
    day5 = summary["unlock_rate_rows"][5]
    food_failure_rate = summary["food_failure_rate"]
    post_unlock_food_failure_rate = summary["food_failure_within_7d_after_unlock_rate"]
    day20_low80 = summary["day_below_rates"][20][80]
    min_balance_median = float(summary["min_balance_stats"][1]) if summary["min_balance_stats"][1] != "n/a" else 0.0

    if (
        food_failure_rate >= 0.08
        or post_unlock_food_failure_rate >= 0.10
        or day20_low80 >= 0.25
        or min_balance_median < 80
    ):
        return "资金风险"
    if day1 >= 0.30 or float(summary["unlock_day_stats"][1]) <= 2.0:
        return "过低"
    if day2 >= 0.50 and day3 >= 0.75 and day4 >= 0.90 and day5 >= 0.95:
        return "目标区间"
    return "过高"


def price_category_ranges(price_summaries: list[dict[str, Any]]) -> dict[str, list[int]]:
    ranges: dict[str, list[int]] = {}
    for summary in price_summaries:
        price = int(summary["unlock_price"])
        ranges.setdefault(summary["category"], []).append(price)
    return ranges


def build_unlock_table(price_summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in price_summaries:
        rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                fmt_pct(summary["unlock_rate_rows"][1]),
                fmt_pct(summary["unlock_rate_rows"][2]),
                fmt_pct(summary["unlock_rate_rows"][3]),
                fmt_pct(summary["unlock_rate_rows"][4]),
                fmt_pct(summary["unlock_rate_rows"][5]),
                fmt_pct(summary["unlock_rate_rows"][7]),
                fmt_pct(summary["unlock_rate_rows"][10]),
                summary["unlock_day_stats"][0],
                summary["unlock_day_stats"][1],
                summary["unlock_day_stats"][2],
                summary["unlock_day_stats"][3],
                summary["first_usable_day_stats"][0],
                summary["first_usable_day_stats"][1],
                summary["first_usable_day_stats"][2],
                summary["first_usable_day_stats"][3],
                fmt_pct(1 - summary["unlock_day20_unlocked_rate"]),
            ]
        )
    return md_table(
        [
            "价格",
            "分类",
            "Day1",
            "Day2",
            "Day3",
            "Day4",
            "Day5",
            "Day7",
            "Day10",
            "解锁日mean",
            "解锁日median",
            "解锁日P10",
            "解锁日P90",
            "Day20未解锁率",
        ],
        rows,
    )


def build_liquidity_table(price_summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in price_summaries:
        rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                fmt_pct(summary["food_failure_rate"]),
                fmt_num(summary["food_failure_total"]),
                fmt_num(summary["food_failure_total_mean"]),
                summary["first_food_failure_stats"][0],
                summary["first_food_failure_stats"][1],
                summary["first_food_failure_stats"][2],
                summary["first_food_failure_stats"][3],
                fmt_num(summary["dining_insufficient_food_total"]),
                *summary["min_balance_stats"],
                fmt_pct(summary["day_below_rates"][5][50]),
                fmt_pct(summary["day_below_rates"][5][80]),
                fmt_pct(summary["day_below_rates"][5][130]),
                fmt_pct(summary["day_below_rates"][10][50]),
                fmt_pct(summary["day_below_rates"][10][80]),
                fmt_pct(summary["day_below_rates"][10][130]),
                fmt_pct(summary["day_below_rates"][20][50]),
                fmt_pct(summary["day_below_rates"][20][80]),
                fmt_pct(summary["day_below_rates"][20][130]),
                fmt_pct(summary["food_failure_within_7d_after_unlock_rate"]),
            ]
        )
    return md_table(
        [
            "价格",
            "分类",
            "食材失败率",
            "失败总次数",
            "平均次数",
            "首次失败mean",
            "首次失败median",
            "首次失败P10",
            "首次失败P90",
            "餐饮缺粮失败数",
            "最低余额mean",
            "最低余额median",
            "最低余额P10",
            "最低余额P90",
            "D5<50",
            "D5<80",
            "D5<130",
            "D10<50",
            "D10<80",
            "D10<130",
            "D20<50",
            "D20<80",
            "D20<130",
            "解锁后7天内失败率",
        ],
        rows,
    )


def build_balance_table(price_summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in price_summaries:
        rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                *summary["end_balance_stats"][5],
                *summary["end_balance_stats"][10],
                *summary["end_balance_stats"][15],
                *summary["end_balance_stats"][20],
                summary["diff_balance_stats"][5][0],
                summary["diff_balance_stats"][10][0],
                summary["diff_balance_stats"][15][0],
                summary["diff_balance_stats"][20][0],
            ]
        )
    return md_table(
        [
            "价格",
            "分类",
            "D5 mean",
            "D5 median",
            "D5 P10",
            "D5 P90",
            "D10 mean",
            "D10 median",
            "D10 P10",
            "D10 P90",
            "D15 mean",
            "D15 median",
            "D15 P10",
            "D15 P90",
            "D20 mean",
            "D20 median",
            "D20 P10",
            "D20 P90",
            "D5较基线均值差",
            "D10较基线均值差",
            "D15较基线均值差",
            "D20较基线均值差",
        ],
        rows,
    )


def build_improvement_table(price_summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in price_summaries:
        rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                fmt_int(summary["overnight_served_delta_total"]),
                fmt_int(summary["overnight_lost_delta_total"]),
                fmt_num(summary["tent2_checkins_total"]),
                fmt_num(summary["tent2_income_total"]),
                fmt_num(summary["total_income"]),
                fmt_num(summary["total_expenses"]),
                fmt_num(summary["balance_delta_total"]),
            ]
        )
    return md_table(
        [
            "价格",
            "分类",
            "过夜接待增量",
            "过夜流失增量",
            "2号帐篷入住收入",
            "2号帐篷住宿收入",
            "总收入",
            "总支出",
            "总净增加",
        ],
        rows,
    )


def build_payback_table(price_summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in price_summaries:
        rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                fmt_pct(summary["payback_rate_by_day"][10]),
                fmt_pct(summary["payback_rate_by_day"][15]),
                fmt_pct(summary["payback_rate_by_day"][20]),
                summary["payback_day_stats"][0],
                summary["payback_day_stats"][1],
                summary["payback_day_stats"][2],
                summary["payback_day_stats"][3],
            ]
        )
    return md_table(
        [
            "价格",
            "分类",
            "D10回本",
            "D15回本",
            "D20回本",
            "回本日mean",
            "回本日median",
            "回本日P10",
            "回本日P90",
        ],
        rows,
    )


def build_demand_table(price_summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in price_summaries:
        rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                fmt_num(summary["natural_overnight_demand_total"]),
                fmt_num(summary["natural_overnight_served_total"]),
                fmt_num(summary["natural_overnight_lost_total"]),
                fmt_pct(
                    summary["natural_overnight_served_total"] / summary["natural_overnight_demand_total"]
                    if summary["natural_overnight_demand_total"]
                    else 0.0
                ),
                fmt_num(summary["reservation_overnight_request_total"]),
                fmt_num(summary["reservation_accepted_overnight_total"]),
                fmt_num(summary["reservation_rejected_capacity_total"]),
                fmt_num(summary["reservation_skipped_existing_total"]),
            ]
        )
    return md_table(
        [
            "价格",
            "分类",
            "自然过夜需求",
            "实际接待",
            "流失",
            "接待率",
            "预约过夜请求",
            "预约过夜成功",
            "预约容量错失",
            "预约既有占用",
        ],
        rows,
    )


def build_report(
    baseline_summary: dict[str, Any],
    price_summaries: list[dict[str, Any]],
    seeds: int,
    days: int,
) -> str:
    category_ranges = price_category_ranges(price_summaries)

    overview = md_table(
        ["项目", "数值"],
        [
            ["模拟规格", f"{seeds} seeds × {days} days"],
            ["完成运行", fmt_int(baseline_summary["completed_runs"])],
            ["阻塞运行", fmt_int(baseline_summary["blocked_runs"])],
            ["负余额运行", fmt_int(baseline_summary["negative_balance_runs"])],
            ["总收入 - 总支出", fmt_num(baseline_summary["ledger_delta_total"])],
            ["期末余额 - 初始余额", fmt_num(baseline_summary["balance_delta_total"])],
            ["账目差值", fmt_num(baseline_summary["ledger_gap_total"])],
        ],
    )

    category_rows = []
    for summary in price_summaries:
        category_rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                fmt_pct(summary["unlock_rate_rows"][1]),
                fmt_pct(summary["unlock_rate_rows"][2]),
                fmt_pct(summary["unlock_rate_rows"][3]),
                fmt_pct(summary["unlock_rate_rows"][4]),
                fmt_pct(summary["unlock_rate_rows"][5]),
                summary["unlock_day_stats"][1],
                fmt_pct(summary["food_failure_rate"]),
                fmt_pct(summary["food_failure_within_7d_after_unlock_rate"]),
            ]
        )
    category_table = md_table(
        ["价格", "分类", "D1", "D2", "D3", "D4", "D5", "解锁日median", "食材失败率", "解锁后7天失败率"],
        category_rows,
    )

    unlock_table = build_unlock_table(price_summaries)
    liquidity_table = build_liquidity_table(price_summaries)
    balance_table = build_balance_table(price_summaries)
    improvement_table = build_improvement_table(price_summaries)
    payback_table = build_payback_table(price_summaries)
    demand_table = build_demand_table(price_summaries)

    lines = [
        "# 2号帐篷早期价格敏感度模拟",
        "",
        "- 基于当前真实 `GameEngine` 运行。",
        "- 2号帐篷只在 Day 2 Turn 6 之后具备购买资格，购买后次日开始使用。",
        "- 模拟中的“符合条件立即购买”只是测试策略，不代表真实玩家会自动购买。",
        "- 食材采购失败只记录，不阻塞流程。",
        "- 850～875 的窄扫描只是诊断过程，不是最终定价结论。",
        "- Day 2 的高可负担率不等于系统自动购买。",
        "- 日期门槛负责阻止 Day 1 过早开放，价格负责资源取舍。",
        "- 已确认第一版规则：Day 2 Turn 6 开放资格，玩家主动购买，800金币，次日生效。",
        "- 800 仍属于 v1 第一版基准值，完整经济平衡时允许统一微调。",
        "",
        "## 运行概览",
        "",
        overview,
        "",
        "## 价格分区",
        "",
        category_table,
        "",
        "## 最早可负担率",
        "",
        unlock_table,
        "",
        "## 流动性风险",
        "",
        liquidity_table,
        "",
        "## 余额与基线对比",
        "",
        balance_table,
        "",
        "## 供需与收入改善",
        "",
        improvement_table,
        "",
        "## 回本情况",
        "",
        payback_table,
        "",
        "## 过夜供需",
        "",
        demand_table,
        "",
        "## 分区摘要",
        "",
        f"- 过低区间: {', '.join(str(v) for v in category_ranges.get('过低', [])) or '无'}",
        f"- 目标区间: {', '.join(str(v) for v in category_ranges.get('目标区间', [])) or '无'}",
        f"- 过高区间: {', '.join(str(v) for v in category_ranges.get('过高', [])) or '无'}",
        f"- 资金风险区间: {', '.join(str(v) for v in category_ranges.get('资金风险', [])) or '无'}",
        f"- 过低 → 目标区间 的首个转折价: {min(category_ranges.get('目标区间', []) or [0]) or '无'}",
        f"- 目标区间 → 过高/资金风险 的首个转折价: {min((category_ranges.get('过高', []) + category_ranges.get('资金风险', [])) or [0]) or '无'}",
    ]
    return "\n".join(lines)


def print_console(baseline_summary: dict[str, Any], price_summaries: list[dict[str, Any]]) -> None:
    print("Tent-2 unlock sensitivity simulation")
    print(f"baseline completed runs: {baseline_summary['completed_runs']}")
    print(f"baseline blocked runs: {baseline_summary['blocked_runs']}")
    print(f"baseline ledger gap: {fmt_num(baseline_summary['ledger_gap_total'])}")
    for summary in price_summaries:
        print(
            f"price {summary['unlock_price']}: "
            f"category={summary['category']} "
            f"unlockD5={fmt_pct(summary['unlock_rate_rows'][5])} "
            f"foodFail={fmt_pct(summary['food_failure_rate'])}"
        )
    if baseline_summary["blocked_runs"]:
        print("blocked details:")
        for item in baseline_summary["blocked_details"]:
            print(
                f"  seed={item.get('seed', '?')} completed={item.get('completed_days', 0)} "
                f"reason={item.get('block_reason', '')}"
            )


def classify_price(summary: dict[str, Any]) -> str:
    day1 = summary["unlock_rate_rows"][1]
    day2 = summary["unlock_rate_rows"][2]
    day3 = summary["unlock_rate_rows"][3]
    day4 = summary["unlock_rate_rows"][4]
    day5 = summary["unlock_rate_rows"][5]
    food_failure_rate = summary["food_failure_rate"]
    post_unlock_food_failure_rate = summary["food_failure_within_7d_after_unlock_rate"]
    day20_low80 = summary["day_below_rates"][20][80]
    unlock_day_median = summary["unlock_day_stats"][1]
    unlock_day_median_value = float(unlock_day_median) if unlock_day_median != "n/a" else 999.0
    min_balance_median = float(summary["min_balance_stats"][1]) if summary["min_balance_stats"][1] != "n/a" else 0.0

    if (
        food_failure_rate >= 0.08
        or post_unlock_food_failure_rate >= 0.10
        or day20_low80 >= 0.25
        or min_balance_median < 80
    ):
        return "资金风险"
    if day1 >= 0.30 or unlock_day_median_value <= 2.0:
        return "过低"
    if day2 >= 0.50 and day3 >= 0.75 and day4 >= 0.90 and day5 >= 0.95:
        return "目标区间"
    return "过高"


def classify_price(summary: dict[str, Any]) -> str:
    day2 = summary["unlock_rate_rows"][2]
    day3 = summary["unlock_rate_rows"][3]
    day4 = summary["unlock_rate_rows"][4]
    day5 = summary["unlock_rate_rows"][5]
    food_failure_rate = summary["food_failure_rate"]
    post_unlock_food_failure_rate = summary["food_failure_within_7d_after_unlock_rate"]
    day20_low80 = summary["day_below_rates"][20][80]
    min_balance_median = float(summary["min_balance_stats"][1]) if summary["min_balance_stats"][1] != "n/a" else 0.0

    if (
        food_failure_rate >= 0.08
        or post_unlock_food_failure_rate >= 0.10
        or day20_low80 >= 0.25
        or min_balance_median < 80
    ):
        return "资金风险"
    if day2 < 0.20 or day3 < 0.60 or day4 < 0.85 or day5 < 0.90:
        return "过高"
    if day2 > 0.40 or day3 > 0.80 or day4 > 0.95:
        return "过低"
    return "目标区间"


def build_unlock_table(price_summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in price_summaries:
        rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                fmt_pct(summary["unlock_rate_rows"][1]),
                fmt_pct(summary["unlock_rate_rows"][2]),
                fmt_pct(summary["unlock_rate_rows"][3]),
                fmt_pct(summary["unlock_rate_rows"][4]),
                fmt_pct(summary["unlock_rate_rows"][5]),
                fmt_pct(summary["unlock_rate_rows"][7]),
                fmt_pct(summary["unlock_rate_rows"][10]),
                summary["unlock_day_stats"][0],
                summary["unlock_day_stats"][1],
                summary["unlock_day_stats"][2],
                summary["unlock_day_stats"][3],
                summary["first_usable_day_stats"][0],
                summary["first_usable_day_stats"][1],
                summary["first_usable_day_stats"][2],
                summary["first_usable_day_stats"][3],
                fmt_pct(1 - summary["unlock_day20_unlocked_rate"]),
            ]
        )
    return md_table(
        [
            "价格",
            "分类",
            "Day1",
            "Day2",
            "Day3",
            "Day4",
            "Day5",
            "Day7",
            "Day10",
            "解锁日mean",
            "解锁日median",
            "解锁日P10",
            "解锁日P90",
            "首个可使用日mean",
            "首个可使用日median",
            "首个可使用日P10",
            "首个可使用日P90",
            "Day20未解锁率",
        ],
        rows,
    )


def build_source_split_table(price_summaries: list[dict[str, Any]]) -> str:
    rows = []
    for summary in price_summaries:
        source_counts = summary["tent2_source_counts_total"]
        source_income = summary["tent2_source_income_total"]
        total_checkins = summary["tent2_checkins_total"] or 1
        rows.append(
            [
                str(summary["unlock_price"]),
                summary["category"],
                fmt_num(source_counts["natural_overnight"]),
                fmt_num(source_counts["overnight_reservation"]),
                fmt_num(source_counts["day_to_overnight"]),
                fmt_num(source_counts["other"]),
                fmt_num(summary["tent2_checkins_total"]),
                fmt_num(source_income["natural_overnight"]),
                fmt_num(source_income["overnight_reservation"]),
                fmt_num(source_income["day_to_overnight"]),
                fmt_num(source_income["other"]),
                fmt_pct(source_counts["natural_overnight"] / total_checkins),
                fmt_pct(source_counts["overnight_reservation"] / total_checkins),
                fmt_pct(source_counts["day_to_overnight"] / total_checkins),
                fmt_pct(source_counts["other"] / total_checkins),
            ]
        )
    return md_table(
        [
            "价格",
            "分类",
            "自然过夜次数",
            "预约过夜次数",
            "日转过夜次数",
            "其他次数",
            "2号帐篷总次数",
            "自然过夜收入",
            "预约过夜收入",
            "日转过夜收入",
            "其他收入",
            "自然过夜占比",
            "预约过夜占比",
            "日转过夜占比",
            "其他占比",
        ],
        rows,
    )


_build_report_base = build_report


def build_report(
    baseline_summary: dict[str, Any],
    price_summaries: list[dict[str, Any]],
    seeds: int,
    days: int,
) -> str:
    report_text = _build_report_base(baseline_summary, price_summaries, seeds, days)
    source_table = build_source_split_table(price_summaries)
    return "\n".join(
        [
            report_text,
            "",
            "## 2号帐篷来源拆分",
            "",
            source_table,
        ]
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.seeds <= 0 or args.days <= 0:
        raise SystemExit("--seeds and --days must be positive integers")

    baseline_runs: list[dict[str, Any]] = []
    scenario_runs: dict[int, list[dict[str, Any]]] = {price: [] for price in PRICE_GRID}

    for seed in range(1, args.seeds + 1):
        baseline_runs.append(run_single_seed(seed, args.days, None))
        for price in PRICE_GRID:
            scenario_runs[price].append(run_single_seed(seed, args.days, price))

    baseline_summary = aggregate_scenario("baseline", None, baseline_runs)
    price_summaries: list[dict[str, Any]] = []
    for price in PRICE_GRID:
        summary = aggregate_scenario(
            str(price),
            price,
            scenario_runs[price],
            baseline_runs=baseline_runs,
        )
        summary["category"] = classify_price(summary)
        price_summaries.append(summary)

    report_text = build_report(baseline_summary, price_summaries, args.seeds, args.days)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_text, encoding="utf-8")

    print_console(baseline_summary, price_summaries)
    print(f"report written to: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
