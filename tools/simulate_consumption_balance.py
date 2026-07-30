from __future__ import annotations

import argparse
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
GAME_ENGINE_DIR = REPO_ROOT / "camping_plaza"
if str(GAME_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(GAME_ENGINE_DIR))

from game_engine import CampingPlazaEngine  # noqa: E402


ARRIVAL_TURNS = (2, 3, 4)
LEVEL_LABELS = {0: "Lv0", 1: "Lv1", 2: "Lv2"}
SPENDING_LABELS = {0: "low", 1: "mid", 2: "high"}
ECONOMIC_LABELS = {0: "low", 1: "mid", 2: "high"}
REPORT_PATH = REPO_ROOT / "docs" / "analysis" / "consumption_balance_v1.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a seeded simulation for dining and entertainment planning."
    )
    parser.add_argument("--samples", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=20260731)
    return parser


def format_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def format_num(value: float) -> str:
    return f"{value:.2f}"


def make_console_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def render(row: list[str]) -> str:
        return " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))

    line = "-+-".join("-" * width for width in widths)
    pieces = [render(headers), line]
    pieces.extend(render(row) for row in rows)
    return "\n".join(pieces)


def make_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    row_lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_line, divider, *row_lines])


def zero_tier_counter() -> Counter[str]:
    return Counter({"basic": 0, "standard": 0, "premium": 0})


def new_module_stats() -> dict[str, Any]:
    return {
        "planned": 0,
        "revenue_sum": 0.0,
        "satisfaction_sum": 0.0,
        "food_sum": 0.0,
        "tiers": zero_tier_counter(),
        "by_spending": {
            habit: {"total": 0, "planned": 0} for habit in SPENDING_LABELS
        },
        "by_economic": {
            econ: {"total": 0, "planned": 0, "tiers": zero_tier_counter()}
            for econ in ECONOMIC_LABELS
        },
    }


def new_free_stats() -> dict[str, Any]:
    return {
        "raw_hit": 0,
        "retained": 0,
        "dropped_due_time": 0,
        "by_arrival_turn": {
            turn: {"total": 0, "raw_hit": 0, "retained": 0, "dropped_due_time": 0}
            for turn in ARRIVAL_TURNS
        },
    }


def new_level_stats(level: int) -> dict[str, Any]:
    return {
        "level": level,
        "groups": 0,
        "planned_actions_sum": 0,
        "combined_income_sum": 0.0,
        "all_three_count": 0,
        "none_count": 0,
        "dining": new_module_stats(),
        "paid_entertainment": new_module_stats(),
        "free_entertainment": new_free_stats(),
    }


def create_engine() -> tuple[CampingPlazaEngine, tempfile.TemporaryDirectory[str]]:
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = Path(temp_dir.name) / "simulation.db"
    engine = CampingPlazaEngine(db_path=str(db_path))
    engine.state.today_arrival_plan = []
    engine.state.today_arrival_plan_day = 0
    return engine, temp_dir


def probe_raw_actions(engine: CampingPlazaEngine, entry: dict[str, Any]) -> dict[str, Any]:
    state = random.getstate()
    try:
        probe_input = {
            "economic_level": entry["economic_level"],
            "spending_habit": entry["spending_habit"],
        }
        return {
            "dining": engine._build_dining_planned_action(dict(probe_input)),
            "paid_entertainment": engine._build_paid_entertainment_planned_action(
                dict(probe_input)
            ),
            "free_entertainment": engine._build_free_entertainment_planned_action(),
        }
    finally:
        random.setstate(state)


def summarize_tier_distribution(counter: Counter[str]) -> list[str]:
    total = sum(counter.values())
    if total == 0:
        return ["0.00%", "0.00%", "0.00%"]
    return [
        format_pct(counter[tier_key] / total)
        for tier_key in ("basic", "standard", "premium")
    ]


def safe_ratio(numerator: float, denominator: float) -> str:
    if denominator == 0:
        return "n/a"
    return f"{numerator / denominator:.2f}"


def simulate_level(level: int, samples: int, seed: int) -> dict[str, Any]:
    engine, temp_dir = create_engine()
    try:
        engine.facilities["dining"].level = level
        engine.facilities["entertainment"].level = level
        random.seed(seed + level)
        stats = new_level_stats(level)

        for sample_index in range(samples):
            arrival_turn = ARRIVAL_TURNS[sample_index % len(ARRIVAL_TURNS)]
            guest = engine._create_day_guest()
            entry = engine._build_arrival_plan_entry(guest, arrival_turn, "simulation")
            raw_actions = probe_raw_actions(engine, entry)
            engine._append_planned_actions(entry)

            stats["groups"] += 1
            stats["free_entertainment"]["by_arrival_turn"][arrival_turn]["total"] += 1

            spending_habit = entry["spending_habit"]
            economic_level = entry["economic_level"]

            for module_key in ("dining", "paid_entertainment"):
                stats[module_key]["by_spending"][spending_habit]["total"] += 1
                stats[module_key]["by_economic"][economic_level]["total"] += 1

            if raw_actions["free_entertainment"] is not None:
                stats["free_entertainment"]["raw_hit"] += 1
                stats["free_entertainment"]["by_arrival_turn"][arrival_turn]["raw_hit"] += 1

            actions_by_name = {
                action["action"]: action for action in entry["planned_actions"]
            }
            stats["planned_actions_sum"] += len(entry["planned_actions"])

            if not entry["planned_actions"]:
                stats["none_count"] += 1

            if {
                "dining",
                "paid_entertainment",
                "free_entertainment",
            }.issubset(actions_by_name):
                stats["all_three_count"] += 1

            dining_action = actions_by_name.get("dining")
            if dining_action is not None:
                menu_key = dining_action["menu_key"]
                menu = engine.DINING_SET_MENUS[menu_key]
                revenue = menu["price_per_person"] * entry["group_size"]
                food_used = entry["group_size"]
                satisfaction = menu["satisfaction_gain"]
                stats["dining"]["planned"] += 1
                stats["dining"]["revenue_sum"] += revenue
                stats["dining"]["food_sum"] += food_used
                stats["dining"]["satisfaction_sum"] += satisfaction
                stats["dining"]["tiers"][menu_key] += 1
                stats["dining"]["by_spending"][spending_habit]["planned"] += 1
                stats["dining"]["by_economic"][economic_level]["planned"] += 1
                stats["dining"]["by_economic"][economic_level]["tiers"][menu_key] += 1
                stats["combined_income_sum"] += revenue

            paid_action = actions_by_name.get("paid_entertainment")
            if paid_action is not None:
                tier_key = paid_action["tier_key"]
                tier = engine.ENTERTAINMENT_TIER_OPTIONS[tier_key]
                revenue = tier["price_per_group"]
                satisfaction = tier["satisfaction_gain"]
                stats["paid_entertainment"]["planned"] += 1
                stats["paid_entertainment"]["revenue_sum"] += revenue
                stats["paid_entertainment"]["satisfaction_sum"] += satisfaction
                stats["paid_entertainment"]["tiers"][tier_key] += 1
                stats["paid_entertainment"]["by_spending"][spending_habit]["planned"] += 1
                stats["paid_entertainment"]["by_economic"][economic_level]["planned"] += 1
                stats["paid_entertainment"]["by_economic"][economic_level]["tiers"][
                    tier_key
                ] += 1
                stats["combined_income_sum"] += revenue

            if "free_entertainment" in actions_by_name:
                stats["free_entertainment"]["retained"] += 1
                stats["free_entertainment"]["by_arrival_turn"][arrival_turn]["retained"] += 1
            elif raw_actions["free_entertainment"] is not None:
                stats["free_entertainment"]["dropped_due_time"] += 1
                stats["free_entertainment"]["by_arrival_turn"][arrival_turn][
                    "dropped_due_time"
                ] += 1

        return stats
    finally:
        try:
            temp_dir.cleanup()
        except PermissionError:
            pass


def level_summary_rows(level_stats: dict[int, dict[str, Any]]) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        stats = level_stats[level]
        groups = stats["groups"]
        dining_avg = stats["dining"]["revenue_sum"] / groups
        paid_avg = stats["paid_entertainment"]["revenue_sum"] / groups
        free_keep = stats["free_entertainment"]["retained"] / groups
        rows.append(
            [
                LEVEL_LABELS[level],
                format_pct(stats["dining"]["planned"] / groups),
                format_pct(stats["paid_entertainment"]["planned"] / groups),
                format_pct(free_keep),
                format_num(dining_avg),
                format_num(paid_avg),
                format_num(stats["combined_income_sum"] / groups),
            ]
        )
    return rows


def module_total_rate_rows(
    level_stats: dict[int, dict[str, Any]], module_key: str
) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        stats = level_stats[level]
        groups = stats["groups"]
        rows.append(
            [
                LEVEL_LABELS[level],
                format_pct(stats[module_key]["planned"] / groups),
            ]
        )
    return rows


def tier_rows(level_stats: dict[int, dict[str, Any]], module_key: str) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        counter = level_stats[level][module_key]["tiers"]
        rows.append([LEVEL_LABELS[level], *summarize_tier_distribution(counter)])
    return rows


def by_spending_rows(level_stats: dict[int, dict[str, Any]], module_key: str) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        for habit in sorted(SPENDING_LABELS):
            bucket = level_stats[level][module_key]["by_spending"][habit]
            rate = bucket["planned"] / bucket["total"] if bucket["total"] else 0.0
            rows.append([LEVEL_LABELS[level], SPENDING_LABELS[habit], format_pct(rate)])
    return rows


def by_economic_rows(level_stats: dict[int, dict[str, Any]], module_key: str) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        for econ in sorted(ECONOMIC_LABELS):
            counter = level_stats[level][module_key]["by_economic"][econ]["tiers"]
            rows.append([LEVEL_LABELS[level], ECONOMIC_LABELS[econ], *summarize_tier_distribution(counter)])
    return rows


def free_rows(level_stats: dict[int, dict[str, Any]]) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        stats = level_stats[level]["free_entertainment"]
        for arrival_turn in ARRIVAL_TURNS:
            bucket = stats["by_arrival_turn"][arrival_turn]
            total = bucket["total"]
            raw_hit = bucket["raw_hit"]
            rows.append(
                [
                    LEVEL_LABELS[level],
                    f"Turn {arrival_turn}",
                    format_pct(raw_hit / total if total else 0.0),
                    format_pct(bucket["retained"] / total if total else 0.0),
                    format_pct(
                        bucket["dropped_due_time"] / raw_hit if raw_hit else 0.0
                    ),
                ]
            )
    return rows


def aggregate_rows(level_stats: dict[int, dict[str, Any]]) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        stats = level_stats[level]
        groups = stats["groups"]
        dining_avg = stats["dining"]["revenue_sum"] / groups
        paid_avg = stats["paid_entertainment"]["revenue_sum"] / groups
        rows.append(
            [
                LEVEL_LABELS[level],
                format_num(stats["planned_actions_sum"] / groups),
                format_pct(stats["all_three_count"] / groups),
                format_pct(stats["none_count"] / groups),
                format_num(stats["combined_income_sum"] / groups),
                safe_ratio(dining_avg, paid_avg),
            ]
        )
    return rows


def metric_rows(level_stats: dict[int, dict[str, Any]], module_key: str) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        stats = level_stats[level]
        groups = stats["groups"]
        module = stats[module_key]
        rows.append(
            [
                LEVEL_LABELS[level],
                format_num(module["revenue_sum"] / groups),
                format_num(module["food_sum"] / groups),
                format_num(module["satisfaction_sum"] / groups),
            ]
        )
    return rows


def paid_metric_rows(level_stats: dict[int, dict[str, Any]]) -> list[list[str]]:
    rows = []
    for level in sorted(level_stats):
        stats = level_stats[level]
        groups = stats["groups"]
        module = stats["paid_entertainment"]
        rows.append(
            [
                LEVEL_LABELS[level],
                format_num(module["revenue_sum"] / groups),
                format_num(module["satisfaction_sum"] / groups),
            ]
        )
    return rows


def build_report(level_stats: dict[int, dict[str, Any]], samples: int, seed: int) -> str:
    dining_rate_rows = by_spending_rows(level_stats, "dining")
    dining_total_rate_rows = module_total_rate_rows(level_stats, "dining")
    dining_tier_rows = tier_rows(level_stats, "dining")
    dining_econ_rows = by_economic_rows(level_stats, "dining")
    dining_metric_rows = metric_rows(level_stats, "dining")

    paid_rate_rows = by_spending_rows(level_stats, "paid_entertainment")
    paid_total_rate_rows = module_total_rate_rows(level_stats, "paid_entertainment")
    paid_tier_rows = tier_rows(level_stats, "paid_entertainment")
    paid_econ_rows = by_economic_rows(level_stats, "paid_entertainment")
    paid_metrics = paid_metric_rows(level_stats)

    free_summary_rows = []
    for level in sorted(level_stats):
        stats = level_stats[level]["free_entertainment"]
        groups = level_stats[level]["groups"]
        raw_hit = stats["raw_hit"]
        free_summary_rows.append(
            [
                LEVEL_LABELS[level],
                format_pct(raw_hit / groups),
                format_pct(stats["retained"] / groups),
                format_pct(stats["dropped_due_time"] / raw_hit if raw_hit else 0.0),
            ]
        )

    sections = [
        "# Consumption Balance V1 Simulation",
        "",
        f"- Samples per facility level: `{samples}`",
        f"- Base seed: `{seed}`",
        "- This is a local simulation for the consumption planning module only, not a full-economy simulation.",
        "- It does not include capacity limits, lodging revenue, food shortages, breakdowns, ratings, or player decisions.",
        "- The current probabilities and weights are still a first-pass balancing set.",
        "",
        "## Dining",
        "",
        "### Total Planned Action Rate",
        "",
        make_markdown_table(["Level", "planned rate"], dining_total_rate_rows),
        "",
        "### Planned Action Rate By Spending Habit",
        "",
        make_markdown_table(["Level", "spending_habit", "planned rate"], dining_rate_rows),
        "",
        "### Tier Distribution By Facility Level",
        "",
        make_markdown_table(["Level", "basic", "standard", "premium"], dining_tier_rows),
        "",
        "### Tier Distribution By Economic Level",
        "",
        make_markdown_table(
            ["Level", "economic_level", "basic", "standard", "premium"],
            dining_econ_rows,
        ),
        "",
        "### Average Per Group",
        "",
        make_markdown_table(
            ["Level", "avg revenue", "avg food use", "avg satisfaction gain"],
            dining_metric_rows,
        ),
        "",
        "## Paid Entertainment",
        "",
        "### Total Planned Action Rate",
        "",
        make_markdown_table(["Level", "planned rate"], paid_total_rate_rows),
        "",
        "### Planned Action Rate By Spending Habit",
        "",
        make_markdown_table(["Level", "spending_habit", "planned rate"], paid_rate_rows),
        "",
        "### Tier Distribution By Facility Level",
        "",
        make_markdown_table(["Level", "basic", "standard", "premium"], paid_tier_rows),
        "",
        "### Tier Distribution By Economic Level",
        "",
        make_markdown_table(
            ["Level", "economic_level", "basic", "standard", "premium"],
            paid_econ_rows,
        ),
        "",
        "### Average Per Group",
        "",
        make_markdown_table(
            ["Level", "avg revenue", "avg satisfaction gain"],
            paid_metrics,
        ),
        "",
        "## Free Entertainment",
        "",
        make_markdown_table(
            ["Level", "raw hit rate", "retained rate", "dropped due to time / raw hits"],
            free_summary_rows,
        ),
        "",
        "### By Arrival Turn",
        "",
        make_markdown_table(
            ["Level", "arrival_turn", "raw hit rate", "retained rate", "drop rate"],
            free_rows(level_stats),
        ),
        "",
        "## Combined",
        "",
        make_markdown_table(
            [
                "Level",
                "avg planned actions",
                "all three actions",
                "no action",
                "avg potential revenue",
                "dining / paid entertainment revenue",
            ],
            aggregate_rows(level_stats),
        ),
        "",
    ]
    return "\n".join(sections)


def print_console_summary(level_stats: dict[int, dict[str, Any]], samples: int, seed: int):
    summary_rows = level_summary_rows(level_stats)
    print("Consumption Balance Simulation V1")
    print(f"samples per level: {samples}")
    print(f"base seed: {seed}")
    print()
    print(
        make_console_table(
            [
                "Level",
                "Dining rate",
                "Paid ent rate",
                "Free keep",
                "Avg dining",
                "Avg paid",
                "Avg total",
            ],
            summary_rows,
        )
    )
    print()
    print(
        make_console_table(
            ["Level", "All three", "No action", "Dining/Paid ratio"],
            [
                [
                    LEVEL_LABELS[level],
                    format_pct(level_stats[level]["all_three_count"] / level_stats[level]["groups"]),
                    format_pct(level_stats[level]["none_count"] / level_stats[level]["groups"]),
                    safe_ratio(
                        level_stats[level]["dining"]["revenue_sum"] / level_stats[level]["groups"],
                        level_stats[level]["paid_entertainment"]["revenue_sum"]
                        / level_stats[level]["groups"],
                    ),
                ]
                for level in sorted(level_stats)
            ],
        )
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.samples <= 0:
        raise SystemExit("--samples must be a positive integer")

    level_stats = {
        level: simulate_level(level, args.samples, args.seed)
        for level in sorted(LEVEL_LABELS)
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report_text = build_report(level_stats, args.samples, args.seed)
    REPORT_PATH.write_text(report_text, encoding="utf-8")

    print_console_summary(level_stats, args.samples, args.seed)
    print()
    print(f"report written to: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
