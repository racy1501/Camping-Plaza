from __future__ import annotations

import argparse
import os
import math
import random
import statistics
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
GAME_ENGINE_DIR = REPO_ROOT / "camping_plaza"
if str(GAME_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(GAME_ENGINE_DIR))

from game_engine import CampingPlazaEngine  # noqa: E402


DEFAULT_SEEDS = 200
DEFAULT_DAYS = 30
REPORT_PATH = REPO_ROOT / "docs" / "analysis" / "economy_baseline_v1.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a baseline economy simulation against the live GameEngine."
    )
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    return parser


def fmt_num(value: float) -> str:
    return f"{value:,.2f}"


def fmt_int(value: float) -> str:
    return f"{int(round(value)):,}"


def fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    divider_line = "| " + " | ".join("---" for _ in headers) + " |"
    row_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, divider_line, *row_lines])


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    data = sorted(values)
    position = (len(data) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return data[lower]
    weight = position - lower
    return data[lower] * (1 - weight) + data[upper] * weight


def stats_row(values: list[float]) -> list[str]:
    if not values:
        return ["n/a", "n/a", "n/a", "n/a"]
    return [
        fmt_num(statistics.mean(values)),
        fmt_num(statistics.median(values)),
        fmt_num(percentile(values, 0.10)),
        fmt_num(percentile(values, 0.90)),
    ]


def create_engine(seed: int) -> tuple[CampingPlazaEngine, tempfile.TemporaryDirectory[str]]:
    random.seed(seed)
    temp_dir_path = Path(
        tempfile.mkdtemp(
            prefix="economy-baseline-",
            dir=os.path.join(
                os.environ.get("TEMP")
                or os.environ.get("TMP")
                or tempfile.gettempdir(),
                "camping_plaza_fix_temp",
            ),
        )
    )
    db_path = temp_dir_path / "baseline.db"
    engine = CampingPlazaEngine(db_path=str(db_path))
    return engine, temp_dir_path


def submit_minimal_plan(engine: CampingPlazaEngine) -> dict[str, Any]:
    broken_ids = [
        tent_id
        for tent_id, tent in engine.tents.items()
        if tent.is_unlocked and tent.status == "broken"
    ]
    cleaning_ids = [
        tent_id
        for tent_id, tent in engine.tents.items()
        if tent.is_unlocked and tent.status == "cleaning"
    ]

    free_actions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    if broken_ids:
        for tent_id in broken_ids[:3]:
            actions.append({"action": "repair_tent", "tent_id": tent_id})
    elif cleaning_ids:
        free_actions.append({"action": "clean_tents", "tent_ids": cleaning_ids})

    return engine.submit_turn_plan(free_actions, actions)


def repair_all_broken_tents(engine: CampingPlazaEngine) -> int:
    repaired = 0
    broken_ids = [
        tent_id
        for tent_id, tent in engine.tents.items()
        if tent.is_unlocked and tent.status == "broken"
    ]
    for tent_id in broken_ids:
        result = engine.repair_tent(tent_id, consume_decision=False)
        if result.get("success"):
            repaired += 1
    return repaired


def clean_all_tents(engine: CampingPlazaEngine) -> bool:
    cleaning_ids = [
        tent_id
        for tent_id, tent in engine.tents.items()
        if tent.is_unlocked and tent.status == "cleaning"
    ]
    if not cleaning_ids:
        return True
    result = engine.clean_tents(cleaning_ids)
    return bool(result.get("success"))


def end_of_day_management(engine: CampingPlazaEngine) -> dict[str, Any]:
    info: dict[str, Any] = {
        "repair_count": 0,
        "food_spend": 0,
        "greenery_spend": 0,
        "food_purchase_success": False,
        "greenery_result": "",
    }

    info["repair_count"] += repair_all_broken_tents(engine)
    if not clean_all_tents(engine):
        raise RuntimeError("clean_tents failed during baseline management")

    before_greenery = engine.state.balance
    greenery_result = engine.manage_greenery("maintain")
    info["greenery_result"] = greenery_result
    info["greenery_spend"] = before_greenery - engine.state.balance

    if engine.state.food_stock < 4:
        before_food = engine.state.balance
        food_result = engine.buy_food_package("small")
        if food_result.get("success"):
            info["food_purchase_success"] = True
            info["food_spend"] = before_food - engine.state.balance
        else:
            raise RuntimeError(f"buy_food_package failed: {food_result.get('message')}")

    return info


def count_fault_events(result: dict[str, Any]) -> int:
    return sum(1 for event in result.get("events", []) if "故障" in event)


def reservation_income_for_day(engine: CampingPlazaEngine) -> float:
    profile = engine.state.daily_demand_profile or {}
    result = profile.get("reservation_result")
    reservation = engine.state.reservation or {}

    if result == "accepted_day":
        return float(engine.CAMPSITE_FEE)
    if result == "accepted_overnight":
        tent_id = reservation.get("tent_id") or engine.state.reserved_tent_id
        if tent_id is None:
            return 0.0
        return float(engine.TENT_PRICES[int(tent_id)])
    return 0.0


def current_day_income_breakdown(engine: CampingPlazaEngine) -> dict[str, float]:
    profile = engine.state.daily_demand_profile or {}
    reservation_income = reservation_income_for_day(engine)
    reservation_result = profile.get("reservation_result")
    campsite_income = float(engine.state.today_income.get("campsite", 0))
    accommodation_income = float(engine.state.today_income.get("accommodation", 0))

    return {
        "day_natural": campsite_income - reservation_income
        if reservation_result == "accepted_day"
        else campsite_income,
        "overnight_natural": accommodation_income - reservation_income
        if reservation_result == "accepted_overnight"
        else accommodation_income,
        "reservation": reservation_income,
        "dining": float(engine.state.today_income.get("dining", 0)),
        "entertainment": float(engine.state.today_income.get("entertainment", 0)),
    }


def run_single_seed(seed: int, days: int) -> dict[str, Any]:
    engine, temp_dir = create_engine(seed)
    try:
        aggregate = {
            "daily_income": [],
            "daily_cash_flow": [],
            "daily_end_balance": [],
            "day_balance_curve": [],
            "day_demand": [],
            "day_served": [],
            "day_lost": [],
            "overnight_demand": [],
            "overnight_served": [],
            "overnight_lost": [],
            "faults": 0,
            "repairs": 0,
            "food_spend": 0.0,
            "greenery_spend": 0.0,
            "revenue": {
                "day_natural": 0.0,
                "overnight_natural": 0.0,
                "reservation": 0.0,
                "dining": 0.0,
                "entertainment": 0.0,
            },
            "negative_balance": False,
            "completed_days": 0,
            "blocked": False,
            "block_reason": "",
            "start_balance": float(engine.state.balance),
            "total_income": 0.0,
            "total_expenses": 0.0,
        }

        while aggregate["completed_days"] < days:
            current_day = engine.state.day
            day_start_balance = engine.state.balance
            profile = engine.state.daily_demand_profile or {}

            while engine.state.day == current_day and engine.state.turn < 6:
                if engine.state.turn in (2, 3, 4, 5):
                    plan_result = submit_minimal_plan(engine)
                    if not plan_result.get("success"):
                        raise RuntimeError(
                            f"turn plan rejected on seed {seed}, day {current_day}, "
                            f"turn {engine.state.turn}: {plan_result.get('message')}"
                        )
                advance_result = engine.advance_turn()
                aggregate["faults"] += count_fault_events(advance_result)
                if engine.state.day != current_day and engine.state.turn != 1:
                    raise RuntimeError(
                        f"unexpected turn jump on seed {seed}, day {current_day}"
                    )

            management_result = end_of_day_management(engine)
            aggregate["repairs"] += management_result["repair_count"]
            aggregate["food_spend"] += management_result["food_spend"]
            aggregate["greenery_spend"] += management_result["greenery_spend"]

            day_income = current_day_income_breakdown(engine)
            total_income = sum(day_income.values())
            day_expenses = float(management_result["food_spend"]) + float(
                management_result["greenery_spend"]
            )
            cash_flow = total_income - day_expenses

            aggregate["daily_income"].append(total_income)
            aggregate["daily_cash_flow"].append(cash_flow)
            aggregate["daily_end_balance"].append(engine.state.balance)
            aggregate["day_balance_curve"].append(engine.state.balance)
            aggregate["total_income"] += total_income
            aggregate["total_expenses"] += day_expenses
            aggregate["day_demand"].append(int(profile.get("natural_day_group_demand", 0)))
            aggregate["day_served"].append(
                sum(
                    1
                    for entry in engine.state.today_arrival_plan
                    if entry.get("source") == "natural_day"
                    and entry.get("planned_day") == current_day
                    and entry.get("arrival_status") == "arrived"
                )
            )
            aggregate["day_lost"].append(
                sum(
                    1
                    for entry in engine.state.today_arrival_plan
                    if entry.get("source") == "natural_day"
                    and entry.get("planned_day") == current_day
                    and entry.get("arrival_status") == "turned_away_full"
                )
            )
            aggregate["overnight_demand"].append(
                int(profile.get("natural_overnight_group_demand", 0))
            )
            aggregate["overnight_served"].append(
                sum(
                    1
                    for entry in engine.state.today_arrival_plan
                    if entry.get("source") == "natural_overnight"
                    and entry.get("planned_day") == current_day
                    and entry.get("arrival_status") == "arrived"
                )
            )
            aggregate["overnight_lost"].append(
                sum(
                    1
                    for entry in engine.state.today_arrival_plan
                    if entry.get("source") == "natural_overnight"
                    and entry.get("planned_day") == current_day
                    and entry.get("arrival_status") == "turned_away_full"
                )
            )

            for key in aggregate["revenue"]:
                aggregate["revenue"][key] += day_income[key]

            if engine.state.balance < 0:
                aggregate["negative_balance"] = True

            aggregate["completed_days"] += 1
            if aggregate["completed_days"] >= days:
                break

            pre_advance_day = engine.state.day
            pre_advance_turn = engine.state.turn
            next_result = engine.advance_turn()
            aggregate["faults"] += count_fault_events(next_result)
            if engine.state.day != pre_advance_day + 1 or engine.state.turn != 1:
                raise RuntimeError(
                    f"failed to enter next day on seed {seed}, day {pre_advance_day}"
                )
            if pre_advance_turn != 6:
                raise RuntimeError(
                    f"day transition attempted from non-management turn on seed {seed}"
                )

        aggregate["final_balance"] = engine.state.balance
        aggregate["balance_delta"] = aggregate["final_balance"] - aggregate["start_balance"]
        aggregate["ledger_delta"] = aggregate["total_income"] - aggregate["total_expenses"]
        aggregate["ledger_gap"] = aggregate["ledger_delta"] - aggregate["balance_delta"]
        if not math.isclose(aggregate["ledger_gap"], 0.0, abs_tol=1e-9):
            raise RuntimeError(
                "ledger mismatch: income-expenses does not match balance change "
                f"(gap={aggregate['ledger_gap']})"
            )
        return aggregate
    except Exception as exc:
        return {
            "blocked": True,
            "block_reason": f"{type(exc).__name__}: {exc}",
            "completed_days": aggregate.get("completed_days", 0) if "aggregate" in locals() else 0,
            "final_balance": engine.state.balance if "engine" in locals() else 0,
            "daily_income": aggregate.get("daily_income", []) if "aggregate" in locals() else [],
            "daily_cash_flow": aggregate.get("daily_cash_flow", []) if "aggregate" in locals() else [],
            "daily_end_balance": aggregate.get("daily_end_balance", []) if "aggregate" in locals() else [],
            "day_balance_curve": aggregate.get("day_balance_curve", []) if "aggregate" in locals() else [],
            "day_demand": aggregate.get("day_demand", []) if "aggregate" in locals() else [],
            "day_served": aggregate.get("day_served", []) if "aggregate" in locals() else [],
            "day_lost": aggregate.get("day_lost", []) if "aggregate" in locals() else [],
            "overnight_demand": aggregate.get("overnight_demand", []) if "aggregate" in locals() else [],
            "overnight_served": aggregate.get("overnight_served", []) if "aggregate" in locals() else [],
            "overnight_lost": aggregate.get("overnight_lost", []) if "aggregate" in locals() else [],
            "faults": aggregate.get("faults", 0) if "aggregate" in locals() else 0,
            "repairs": aggregate.get("repairs", 0) if "aggregate" in locals() else 0,
            "food_spend": aggregate.get("food_spend", 0.0) if "aggregate" in locals() else 0.0,
            "greenery_spend": aggregate.get("greenery_spend", 0.0) if "aggregate" in locals() else 0.0,
            "revenue": aggregate.get("revenue", {}) if "aggregate" in locals() else {},
            "negative_balance": aggregate.get("negative_balance", False) if "aggregate" in locals() else False,
            "start_balance": aggregate.get("start_balance", 0.0) if "aggregate" in locals() else 0.0,
            "total_income": aggregate.get("total_income", 0.0) if "aggregate" in locals() else 0.0,
            "total_expenses": aggregate.get("total_expenses", 0.0) if "aggregate" in locals() else 0.0,
            "balance_delta": aggregate.get("balance_delta", 0.0) if "aggregate" in locals() else 0.0,
            "ledger_delta": aggregate.get("ledger_delta", 0.0) if "aggregate" in locals() else 0.0,
            "ledger_gap": aggregate.get("ledger_gap", 0.0) if "aggregate" in locals() else 0.0,
        }
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def aggregate_results(results: list[dict[str, Any]], days: int) -> dict[str, Any]:
    completed = [result for result in results if not result.get("blocked")]
    blocked = [result for result in results if result.get("blocked")]

    daily_income_samples: list[float] = []
    daily_cash_flow_samples: list[float] = []
    end_balance_samples: list[float] = []
    balance_curve_by_day: list[list[float]] = [[] for _ in range(days)]
    day_demand_total = 0
    day_served_total = 0
    day_lost_total = 0
    overnight_demand_total = 0
    overnight_served_total = 0
    overnight_lost_total = 0
    total_faults = 0
    total_repairs = 0
    total_food_spend = 0.0
    total_greenery_spend = 0.0
    total_income = 0.0
    total_expenses = 0.0
    start_balance_total = 0.0
    balance_delta_total = 0.0
    ledger_delta_total = 0.0
    ledger_gap_total = 0.0
    revenue_totals = {
        "day_natural": 0.0,
        "overnight_natural": 0.0,
        "reservation": 0.0,
        "dining": 0.0,
        "entertainment": 0.0,
    }
    negative_balance_runs = 0
    completed_days_total = 0

    for result in completed:
        completed_days_total += result["completed_days"]
        if result.get("negative_balance"):
            negative_balance_runs += 1
        total_faults += result["faults"]
        total_repairs += result["repairs"]
        total_food_spend += result["food_spend"]
        total_greenery_spend += result["greenery_spend"]
        total_income += result.get("total_income", 0.0)
        total_expenses += result.get("total_expenses", 0.0)
        start_balance_total += result.get("start_balance", 0.0)
        balance_delta_total += result.get("balance_delta", 0.0)
        ledger_delta_total += result.get("ledger_delta", 0.0)
        ledger_gap_total += result.get("ledger_gap", 0.0)
        for key in revenue_totals:
            revenue_totals[key] += result["revenue"].get(key, 0.0)

        for day_index, value in enumerate(result["daily_income"]):
            daily_income_samples.append(float(value))
        for day_index, value in enumerate(result["daily_cash_flow"]):
            daily_cash_flow_samples.append(float(value))
        for day_index, value in enumerate(result["daily_end_balance"]):
            if day_index < days:
                balance_curve_by_day[day_index].append(float(value))
        day_demand_total += sum(result["day_demand"])
        day_served_total += sum(result["day_served"])
        day_lost_total += sum(result["day_lost"])
        overnight_demand_total += sum(result["overnight_demand"])
        overnight_served_total += sum(result["overnight_served"])
        overnight_lost_total += sum(result["overnight_lost"])
        if result["daily_end_balance"]:
            end_balance_samples.append(float(result["daily_end_balance"][-1]))

    share_base = sum(revenue_totals.values())
    revenue_shares = {
        key: (value / share_base if share_base else 0.0)
        for key, value in revenue_totals.items()
    }
    daily_balance_curve = [
        statistics.mean(day_values) if day_values else 0.0
        for day_values in balance_curve_by_day
    ]

    return {
        "completed_runs": len(completed),
        "blocked_runs": len(blocked),
        "blocked_details": blocked,
        "daily_income_stats": stats_row(daily_income_samples),
        "daily_cash_flow_stats": stats_row(daily_cash_flow_samples),
        "end_balance_stats": stats_row(end_balance_samples),
        "revenue_totals": revenue_totals,
        "revenue_shares": revenue_shares,
        "food_spend_total": total_food_spend,
        "greenery_spend_total": total_greenery_spend,
        "other_expense_total": max(0.0, total_expenses - total_food_spend - total_greenery_spend),
        "total_income_total": total_income,
        "total_expenses_total": total_expenses,
        "start_balance_total": start_balance_total,
        "balance_delta_total": balance_delta_total,
        "ledger_delta_total": ledger_delta_total,
        "ledger_gap_total": ledger_gap_total,
        "day_demand_total": day_demand_total,
        "day_served_total": day_served_total,
        "day_lost_total": day_lost_total,
        "overnight_demand_total": overnight_demand_total,
        "overnight_served_total": overnight_served_total,
        "overnight_lost_total": overnight_lost_total,
        "faults": total_faults,
        "repairs": total_repairs,
        "negative_balance_runs": negative_balance_runs,
        "daily_balance_curve": daily_balance_curve,
        "completed_days_total": completed_days_total,
    }


def build_report(summary: dict[str, Any], seeds: int, days: int) -> str:
    completed_runs = summary["completed_runs"]
    blocked_runs = summary["blocked_runs"]
    total_runs = completed_runs + blocked_runs
    total_run_label = f"{completed_runs}/{total_runs}"

    income_table = md_table(
        ["Metric", "mean", "median", "P10", "P90"],
        [
            ["每日总收入", *summary["daily_income_stats"]],
            ["每日净现金流", *summary["daily_cash_flow_stats"]],
        ],
    )

    end_balance_table = md_table(
        ["Metric", "mean", "median", "P10", "P90"],
        [["30 天末余额", *summary["end_balance_stats"]]],
    )

    revenue_rows = []
    revenue_labels = {
        "day_natural": "日间自然收入",
        "overnight_natural": "过夜自然收入",
        "reservation": "预约收入",
        "dining": "餐饮收入",
        "entertainment": "娱乐收入",
    }
    for key in ("day_natural", "overnight_natural", "reservation", "dining", "entertainment"):
        revenue_rows.append(
            [
                revenue_labels[key],
                fmt_num(summary["revenue_totals"][key]),
                fmt_pct(summary["revenue_shares"][key]),
            ]
        )

    revenue_table = md_table(["来源", "累计金额", "占比"], revenue_rows)

    expense_table = md_table(
        ["鏀嚭椤圭洰", "绱閲戦"],
        [
            ["椋熸潗鏀嚭", fmt_num(summary["food_spend_total"])],
            ["缁垮寲缁存姢鏀嚭", fmt_num(summary["greenery_spend_total"])],
            ["鍏朵粬瀹為檯鏀嚭", fmt_num(summary["other_expense_total"])],
        ],
    )

    ledger_check_table = md_table(
        ["鏍稿椤圭洰", "閲戦"],
        [
            [
                "鎬绘敹鍏? - 鎬绘敮鍑?",
                fmt_num(summary["total_income_total"] - summary["total_expenses_total"]),
            ],
            [
                "鏈湡鏈缁撹繑 - 鍒濆浣欓",
                fmt_num(summary["balance_delta_total"]),
            ],
            ["宸€?", fmt_num(summary["ledger_gap_total"])],
        ],
    )

    demand_table = md_table(
        ["椤圭洰", "鎬婚渶姹?", "鎬绘帴寰?", "鎬绘祦澶?"] ,
        [
            [
                "鏃ラ棿",
                fmt_int(summary["day_demand_total"]),
                fmt_int(summary["day_served_total"]),
                fmt_int(summary["day_lost_total"]),
            ],
            [
                "杩囧",
                fmt_int(summary["overnight_demand_total"]),
                fmt_int(summary["overnight_served_total"]),
                fmt_int(summary["overnight_lost_total"]),
            ],
        ],
    )

    fault_table = md_table(
        ["指标", "次数"],
        [
            ["故障次数", fmt_int(summary["faults"])],
            ["维修次数", fmt_int(summary["repairs"])],
            ["负余额运行次数", fmt_int(summary["negative_balance_runs"])],
        ],
    )

    balance_curve_rows = [
        [f"Day {index}", fmt_num(value)]
        for index, value in enumerate(summary["daily_balance_curve"], start=1)
    ]
    balance_curve_table = md_table(["天数", "平均余额"], balance_curve_rows)

    blockers_section = "无。"
    if blocked_runs:
        block_rows = [
            [f"Seed {item.get('seed', '?')}", str(item.get("completed_days", 0)), item.get("block_reason", "")]
            for item in summary["blocked_details"]
        ]
        blockers_section = md_table(["Seed", "已完成天数", "阻塞原因"], block_rows)

    note_lines = [
        "# Economy Baseline V1",
        "",
        "- 这是基于当前真实 `GameEngine` 的开局经济基线模拟。",
        "- 报告中的所有现有金币价格均视为当前代码基准值，后续统一校准。",
        "- 本轮不修改业务代码，不推导最终定价，不触碰 `stash@{0}`。",
        f"- 模拟规模：`{seeds}` 个随机种子 × `30` 个游戏日。",
        f"- 成功完成运行：`{total_run_label}`。",
        "",
        "## 运行结果",
        "",
        income_table,
        "",
        end_balance_table,
        "",
        "## 收入来源占比",
        "",
        revenue_table,
        "",
        "## 支出",
        "",
        expense_table,
        "",
        "## 璐︾洰鏍稿",
        "",
        ledger_check_table,
        "",
        "## 闇€姹備笌鎺ュ緟",
        "",
        demand_table,
        "",
        "## 故障与维修",
        "",
        fault_table,
        "",
        "## 每日平均余额曲线",
        "",
        balance_curve_table,
        "",
        "## 阻塞点",
        "",
        blockers_section,
        "",
    ]
    return "\n".join(note_lines)


def print_console(summary: dict[str, Any], seeds: int, days: int) -> None:
    print("Economy baseline simulation")
    print(f"requested runs: {seeds} seeds x {days} days")
    print(f"completed runs: {summary['completed_runs']}")
    print(f"blocked runs: {summary['blocked_runs']}")
    print(
        f"daily income mean/median/p10/p90: {summary['daily_income_stats']}"
    )
    print(
        f"daily cash flow mean/median/p10/p90: {summary['daily_cash_flow_stats']}"
    )
    print(f"30-day end balance mean/median/p10/p90: {summary['end_balance_stats']}")
    print(f"negative balance runs: {summary['negative_balance_runs']}")
    print(f"faults: {fmt_int(summary['faults'])}, repairs: {fmt_int(summary['repairs'])}")
    if summary["blocked_runs"]:
        print("blocked details:")
        for item in summary["blocked_details"]:
            print(
                f"  seed={item.get('seed', '?')} days={item.get('completed_days', 0)} "
                f"reason={item.get('block_reason', '')}"
            )


def main() -> int:
    args = build_parser().parse_args()
    if args.seeds <= 0 or args.days <= 0:
        raise SystemExit("--seeds and --days must be positive integers")

    results: list[dict[str, Any]] = []
    for seed in range(1, args.seeds + 1):
        result = run_single_seed(seed, args.days)
        result["seed"] = seed
        results.append(result)

    summary = aggregate_results(results, args.days)
    report_text = build_report(summary, args.seeds, args.days)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_text, encoding="utf-8")
    print_console(summary, args.seeds, args.days)
    print(f"report written to: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
