#!/usr/bin/env python3
"""使用正式引擎批量观察客组体验账本；不会读取或修改生产存档。"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from camping_plaza.game_engine import CampingPlazaEngine  # noqa: E402
from balance_sim_v1 import day_end_actions, turn_actions  # noqa: E402


LAMBDAS = (1.00, 1.25, 1.50, 1.75, 2.00)
NEGATIVE_THRESHOLDS = (2, 5, 10)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def distribution(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values) if values else 0.0,
        "median": statistics.median(values) if values else 0.0,
        "p10": percentile(values, 0.10), "p25": percentile(values, 0.25),
        "p50": percentile(values, 0.50), "p75": percentile(values, 0.75),
        "p90": percentile(values, 0.90), "p95": percentile(values, 0.95),
        "min": min(values) if values else 0.0, "max": max(values) if values else 0.0,
    }


def simulate_seed(seed: int, days: int, strategy: str) -> dict[str, Any]:
    random.seed(seed)
    # 模拟只需要单个运行态，不需要跨进程恢复；内存 SQLite 因此不会触及生产存档。
    engine = CampingPlazaEngine(":memory:")
    records: list[dict[str, float]] = []
    recorded_ids: set[int] = set()
    conflict_days: list[bool] = []
    conflict_eligible_days: list[bool] = []
    original_review = engine._try_leave_review

    def capture_departure(npc: Any, result: dict) -> None:
        if npc.id not in recorded_ids:
            recorded_ids.add(npc.id)
            records.append({
                "initial": 60.0,
                "final": float(npc.total_satisfaction),
                "positive": float(npc.positive_experience_total),
                "negative": float(npc.negative_experience_total),
            })
        original_review(npc, result)

    engine._try_leave_review = capture_departure  # type: ignore[method-assign]
    try:
        while engine.state.day <= days:
            conflict_eligible_days.append(len(engine.state.today_arrival_plan) >= 2)
            conflict_days.append(
                isinstance(engine.state.today_conflict_event, dict)
                and engine.state.today_conflict_event.get("status") == "scheduled"
            )
            while engine.state.turn <= 5:
                if engine.get_current_temporary_conflict_event() is not None:
                    engine.resolve_current_temporary_conflict("verbal")
                if engine.state.turn in (2, 3, 4, 5):
                    free_actions, actions = turn_actions(engine, strategy)
                    submitted = engine.submit_turn_plan(free_actions, actions)
                    if not submitted.get("success"):
                        raise RuntimeError(f"turn plan rejected: {submitted}")
                engine.advance_turn()
            result = engine.submit_day_end_actions(day_end_actions(engine, strategy))
            if not result.get("success"):
                raise RuntimeError(f"day end rejected: {result}")
            if engine.state.day == days:
                break
            started = engine.start_next_day()
            if not started.get("success"):
                raise RuntimeError(f"start next day rejected: {started}")
        return {
            "records": records,
            "conflict_days": conflict_days,
            "conflict_eligible_days": conflict_eligible_days,
        }
    finally:
        engine._try_leave_review = original_review  # type: ignore[method-assign]


def star_summary(scores: list[float], engine: CampingPlazaEngine) -> dict[str, Any]:
    stars = Counter(engine._calculate_rating(score) for score in scores)
    total = len(scores)
    return {
        "stars": {star: stars[star] / total if total else 0.0 for star in range(1, 6)},
        "average": statistics.fmean([engine._calculate_rating(score) for score in scores]) if total else 0.0,
        "two_or_less": sum(stars[star] for star in (1, 2)) / total if total else 0.0,
        "four_or_more": sum(stars[star] for star in (4, 5)) / total if total else 0.0,
        "five": stars[5] / total if total else 0.0,
    }


def print_distribution(label: str, values: list[float]) -> None:
    stats = distribution(values)
    print(label + ": " + " ".join(f"{key}={value:.2f}" for key, value in stats.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description="客组体验账本 baseline 模拟")
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--runs", type=int, default=500, help="批量独立 seed 数")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--strategy", choices=("growth_priority", "balanced", "quality_priority"), default="balanced")
    args = parser.parse_args()
    if args.runs <= 0 or args.days <= 0:
        parser.error("--runs 和 --days 必须大于 0")

    all_records: list[dict[str, float]] = []
    all_conflict_days: list[bool] = []
    all_conflict_eligible_days: list[bool] = []
    for index in range(args.runs):
        run = simulate_seed(args.seed + index, args.days, args.strategy)
        all_records.extend(run["records"])
        all_conflict_days.extend(run["conflict_days"])
        all_conflict_eligible_days.extend(run["conflict_eligible_days"])

    probe_engine = CampingPlazaEngine(":memory:")
    finals = [record["final"] for record in all_records]
    positives = [record["positive"] for record in all_records]
    negatives = [record["negative"] for record in all_records]
    print(f"Experience baseline | runs={args.runs} days={args.days} seed={args.seed} strategy={args.strategy}")
    print(f"settled_groups={len(all_records)}")
    print_distribution("final_satisfaction", finals)
    print_distribution("positive_experience_total", positives)
    print_distribution("negative_experience_total", negatives)
    total = len(all_records)
    print("negative_experience: " + " ".join([
        f"zero={sum(value == 0 for value in negatives) / total:.2%}",
        f"gt_zero={sum(value > 0 for value in negatives) / total:.2%}",
    ]))
    baseline = star_summary(finals, probe_engine)
    print("formal_stars: " + " ".join(f"{star}★={baseline['stars'][star]:.2%}" for star in range(1, 6)))
    for threshold in NEGATIVE_THRESHOLDS:
        selected = [record for record in all_records if record["negative"] >= threshold]
        count = len(selected)
        stars = [probe_engine._calculate_rating(record["final"]) for record in selected]
        print(
            f"negative>={threshold}: count={count} ratio={count / total:.2%} "
            f"4★={sum(star == 4 for star in stars) / count if count else 0:.2%} "
            f"5★={sum(star == 5 for star in stars) / count if count else 0:.2%}"
        )
    print("candidate_weighted_stars:")
    for weight in LAMBDAS:
        scores = [record["initial"] + record["positive"] - weight * record["negative"] for record in all_records]
        summary = star_summary(scores, probe_engine)
        print(
            f"lambda={weight:.2f} "
            + " ".join(f"{star}★={summary['stars'][star]:.2%}" for star in range(1, 6))
            + f" avg={summary['average']:.2f} <=2★={summary['two_or_less']:.2%} >=4★={summary['four_or_more']:.2%} 5★={summary['five']:.2%}"
        )
    runs_with_conflicts = sum(all_conflict_days)
    print(f"temporary_conflict: daily_trigger_rate={runs_with_conflicts / len(all_conflict_days):.2%} ({runs_with_conflicts}/{len(all_conflict_days)})")
    eligible_count = sum(all_conflict_eligible_days)
    print(
        "temporary_conflict_eligible: "
        f"days={eligible_count}/{len(all_conflict_eligible_days)} "
        f"trigger_rate={runs_with_conflicts / eligible_count if eligible_count else 0:.2%}"
    )
    for length in range(2, 7):
        windows = len(all_conflict_days) - length + 1
        hits = sum(all(all_conflict_days[index:index + length]) for index in range(windows))
        print(f"temporary_conflict_consecutive_{length}_days={hits / windows if windows else 0:.2%} ({hits}/{windows})")


if __name__ == "__main__":
    main()
