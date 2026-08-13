"""
露营广场 - API 接口层
提供给合集站MCP封装 + 前端状态查询
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional
import uvicorn

from game_engine import CampingPlazaEngine


app = FastAPI(title="露营广场 API", version="0.1.0")

# 正式存档路径固定在本文件所在目录，不依赖启动时当前工作目录
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camping_plaza.db")

# 前端资源目录，同样基于本文件所在目录计算
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# CORS 配置（允许合集站前端访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 游戏引擎实例（单例）
engine: Optional[CampingPlazaEngine] = None


def get_engine() -> CampingPlazaEngine:
    global engine
    if engine is None:
        engine = CampingPlazaEngine(db_path=DB_PATH)
    return engine


# =============================================================================
# 请求/响应模型
# =============================================================================

class ActionRequest(BaseModel):
    action: str
    params: Optional[dict] = None


class TurnPlanRequest(BaseModel):
    free_actions: list[ActionRequest] = Field(default_factory=list)
    actions: list[ActionRequest] = Field(default_factory=list)
    conflict_choice: Optional[str] = None


class DayEndRequest(BaseModel):
    day_end_actions: list[ActionRequest] = Field(default_factory=list)


TURN_PLAN_IMMEDIATE_ACTIONS = {
    name for name, config in CampingPlazaEngine.TURN_PLAN_ACTIONS.items()
    if config["kind"] in {"free", "decision"}
}


def _food_package_action_entries() -> list[dict]:
    entries = []
    for package_key, package in CampingPlazaEngine.FOOD_PACKAGES.items():
        entries.append({
            "action": "buy_food_package",
            "params": {"package_key": package_key},
            "cost": package["price"],
            "description": (
                f"购买{package['name']}（{package['portions']}份，{package['price']}金币）"
            )
        })
    return entries


def _food_package_plan_description() -> str:
    package_bits = []
    for package_key, package in CampingPlazaEngine.FOOD_PACKAGES.items():
        package_bits.append(
            f"{package_key}={package['name']}({package['portions']}份/{package['price']}金币)"
        )
    package_text = "，".join(package_bits)
    return (
        "提交本轮营业计划（free_actions支持clean_tents，"
        "actions支持repair_tent、improve_service、buy_food_package，"
        "buy_food_package使用package_key，"
        f"可选包：{package_text}，actions最多3项）"
    )


def _raise_action_request_error(error_code: str, message: str):
    """POST /api/action 的请求语义错误：400 + 机器可读 error_code + 中文 message"""
    raise HTTPException(
        status_code=400,
        detail={
            "error_code": error_code,
            "message": message,
        },
    )


def _normalize_turn_plan_actions(actions: list[ActionRequest]) -> list[dict]:
    normalized = []
    for item in actions:
        params = item.params or {}
        if not isinstance(params, dict):
            raise HTTPException(400, "params必须为对象")
        normalized.append({"action": item.action, **params})
    return normalized


def _normalize_day_end_actions(actions: list[ActionRequest]) -> list[dict]:
    """把 DayEndRequest.day_end_actions 转为引擎 submit_day_end_actions 期望的
    [{"action": ..., "params": {...}}, ...] 结构，params 原样透传不新增字段。"""
    normalized = []
    for item in actions:
        params = item.params or {}
        if not isinstance(params, dict):
            raise HTTPException(400, "params必须为对象")
        normalized.append({"action": item.action, "params": params})
    return normalized


def _get_turn_plan_status(eng: CampingPlazaEngine) -> tuple[bool, bool, Optional[int]]:
    plan = eng.state.pending_turn_plan
    has_current_plan = bool(
        plan and (plan.get("target_day"), plan.get("target_turn")) == (eng.state.day, eng.state.turn)
    )
    plan_target_turn = plan.get("target_turn") if has_current_plan else None
    planning_available = eng.state.turn in (2, 3, 4, 5) and not has_current_plan
    return planning_available, has_current_plan, plan_target_turn


def _safe_turn_plan_action(item) -> Optional[dict]:
    """把单个 turn plan action 映射为安全摘要（显式白名单，不返回内部引用）。"""
    if not isinstance(item, dict):
        return None
    action = item.get("action")
    if action == "clean_tents":
        tent_ids = item.get("tent_ids")
        if tent_ids is None:
            return {"action": "clean_tents"}
        if isinstance(tent_ids, list):
            return {"action": "clean_tents", "params": {"tent_ids": list(tent_ids)}}
        return {"action": "clean_tents"}
    if action == "repair_tent":
        if "tent_id" in item:
            return {"action": "repair_tent", "params": {"tent_id": item["tent_id"]}}
        return {"action": "repair_tent"}
    if action == "improve_service":
        return {"action": "improve_service"}
    if action == "buy_food_package":
        if "package_key" in item:
            return {"action": "buy_food_package", "params": {"package_key": item["package_key"]}}
        return {"action": "buy_food_package"}
    return None


def _get_turn_plan_summary(eng: CampingPlazaEngine) -> Optional[dict]:
    """已提交 Turn Plan 的只读安全摘要；无计划时返回 None。"""
    plan = eng.state.pending_turn_plan
    if plan is None:
        return None
    return {
        "target_day": plan.get("target_day"),
        "target_turn": plan.get("target_turn"),
        "free_actions": [
            s for s in (_safe_turn_plan_action(a) for a in plan.get("free_actions", []))
            if s is not None
        ],
        "decision_actions": [
            s for s in (_safe_turn_plan_action(a) for a in plan.get("actions", []))
            if s is not None
        ],
    }


def _build_neutral_turn_action_candidates(eng: CampingPlazaEngine) -> dict:
    """生成 Turn 2～5 共用的中性动作候选，不包含 UI 文案。"""
    state = eng.get_full_state()
    balance = eng.state.balance
    turn = eng.state.turn
    cleaning_tent_ids = [
        int(tid) for tid, tent in state["tents"].items()
        if tent["unlocked"] and tent["status"] == "cleaning"
    ]
    free_candidates = [{
        "action": "clean_tents",
        "kind": "free",
        "enabled": bool(cleaning_tent_ids),
        "reason": "" if cleaning_tent_ids else "暂无待清洁帐篷",
        "params": {"tent_ids": cleaning_tent_ids},
        "repeatable": False,
        "cost_decision_points": 0,
    }]
    decision_candidates = []
    for tid, tent in state["tents"].items():
        if not (tent["unlocked"] and tent["status"] == "broken"):
            continue
        tent_id = int(tid)
        enabled = balance >= CampingPlazaEngine.REPAIR_COST
        decision_candidates.append({
            "action": "repair_tent", "kind": "decision",
            "enabled": enabled,
            "reason": "" if enabled else "金币不足",
            "params": {"tent_id": tent_id}, "repeatable": False,
            "price": CampingPlazaEngine.REPAIR_COST,
            "cost_decision_points": 1,
        })

    improve_remaining = max(0, 2 - eng.state.improve_service_uses_today)
    decision_candidates.append({
        "action": "improve_service", "kind": "decision",
        "enabled": improve_remaining > 0,
        "reason": "" if improve_remaining else "今日提升服务次数已达到上限",
        "params": {}, "repeatable": False,
        "remaining_today": improve_remaining, "cost_decision_points": 1,
    })
    clean_remaining = max(0, 2 - eng.state.clean_campsite_uses_today)
    decision_candidates.append({
        "action": "clean_campsite", "kind": "decision",
        "enabled": clean_remaining > 0,
        "reason": "" if clean_remaining else "今日清洁营地次数已达到上限",
        "params": {}, "repeatable": False,
        "remaining_today": clean_remaining, "cost_decision_points": 1,
    })
    post_remaining = 0 if eng.state.post_used_today else 1
    decision_candidates.append({
        "action": "make_post", "kind": "decision",
        "enabled": post_remaining > 0,
        "reason": "" if post_remaining else "今天已经发布过帖子",
        "params": {}, "repeatable": False,
        "remaining_today": post_remaining, "cost_decision_points": 1,
    })
    if turn == 4:
        decision_candidates.append({
            "action": "campfire", "kind": "decision", "enabled": True,
            "reason": "", "params": {}, "repeatable": False,
            "cost_decision_points": 1,
        })
    if turn == 5:
        decision_candidates.append({
            "action": "stargazing", "kind": "decision", "enabled": True,
            "reason": "", "params": {}, "repeatable": False,
            "cost_decision_points": 1,
        })
    for package_key, package in CampingPlazaEngine.FOOD_PACKAGES.items():
        enabled = balance >= package["price"]
        decision_candidates.append({
            "action": "buy_food_package", "kind": "decision",
            "enabled": enabled, "reason": "" if enabled else "金币不足",
            "params": {"package_key": package_key}, "repeatable": True,
            "price": package["price"], "portions": package["portions"],
            "max_quantity": 3, "cost_decision_points": 1,
        })
    return {
        "free_action_candidates": free_candidates,
        "decision_action_candidates": decision_candidates,
    }


def _build_human_action_catalog(eng: CampingPlazaEngine) -> dict:
    """把引擎状态整理为适合人类网页读取的动作目录。只读，不修改状态。"""
    state = eng.get_full_state()
    planning_available, plan_submitted, _plan_target_turn = _get_turn_plan_status(eng)
    turn = eng.state.turn
    balance = eng.state.balance
    day_end_completed = eng.state.day_end_completed

    # Turn 1：迎客准备
    if turn == 1:
        return {
            "success": True,
            "day": state["day"],
            "turn": turn,
            "mode": "opening",
            "panel_title": "迎客准备",
            "planning_available": False,
            "plan_submitted": False,
            "max_decision_actions": 3,
            "turn_plan": None,
            "free_action_candidates": [],
            "decision_action_candidates": [],
            "primary_action": {
                "action": "advance_turn",
                "label": "开始营业",
                "enabled": True,
                "reason": "",
            },
        }

    # Turn 6：日终阶段，本轮只返回阶段标识
    if turn == 6:
        if day_end_completed:
            mode = "day_end_completed"
            panel_title = "日终管理"
            day_end_action_candidates = []
        else:
            mode = "day_end_pending"
            panel_title = "日终管理"
            day_end_action_candidates = []

            cleaning_tent_ids = [
                int(tid) for tid, t in state["tents"].items()
                if t["unlocked"] and t["status"] == "cleaning"
            ]
            if cleaning_tent_ids and "clean_tents" in CampingPlazaEngine.DAY_END_ACTIONS:
                day_end_action_candidates.append({
                    "action": "clean_tents",
                    "params": {"tent_ids": cleaning_tent_ids},
                    "label": "清洁待清洁帐篷",
                    "kind": "day_end",
                    "enabled": True,
                    "reason": "",
                })

            for tid, tent in state["tents"].items():
                if not (tent["unlocked"] and tent["status"] == "broken"):
                    continue
                enabled = balance >= CampingPlazaEngine.REPAIR_COST
                day_end_action_candidates.append({
                    "action": "repair_tent",
                    "params": {"tent_id": int(tid)},
                    "cost": CampingPlazaEngine.REPAIR_COST,
                    "label": f"维修{tid}号帐篷",
                    "kind": "day_end",
                    "enabled": enabled,
                    "reason": "" if enabled else "金币不足",
                })

            greenery_value = state.get("greenery", {})
            greenery_max_level = greenery_value.get("level", 0) >= 2
            if (
                greenery_value.get("value", 0) > 0
                and "manage_greenery" in CampingPlazaEngine.DAY_END_ACTIONS
                and (greenery_max_level or not eng.state.greenery_processed_today)
            ):
                enabled = balance >= 50 and not greenery_max_level
                day_end_action_candidates.append({
                    "action": "manage_greenery",
                    "params": {"action": "maintain"},
                    "cost": 50,
                    "label": "打理绿化",
                    "kind": "day_end",
                    "enabled": enabled,
                    "reason": "已满级" if greenery_max_level else ("" if enabled else "金币不足"),
                })

            if (
                eng.state.last_food_preorder_day != eng.state.day
                and "buy_food_package" in CampingPlazaEngine.DAY_END_ACTIONS
            ):
                for entry in _food_package_action_entries():
                    package_key = entry["params"]["package_key"]
                    package = CampingPlazaEngine.FOOD_PACKAGES[package_key]
                    day_end_action_candidates.append({
                        "action": "buy_food_package",
                        "params": {"package_key": package_key},
                        "cost": package["price"],
                        "label": entry["description"],
                        "kind": "day_end",
                        "enabled": balance >= package["price"],
                        "reason": "" if balance >= package["price"] else "金币不足",
                    })

            if "purchase_growth_project" in CampingPlazaEngine.DAY_END_ACTIONS:
                for project in eng.get_growth_project_catalog():
                    if not project.get("can_purchase_now"):
                        continue
                    day_end_action_candidates.append({
                        "action": "purchase_growth_project",
                        "params": {"project_id": project["project_id"]},
                        "cost": project["price"],
                        "label": f"购买{project['display_name']}",
                        "kind": "day_end",
                        "enabled": True,
                        "reason": "",
                    })
        return {
            "success": True,
            "day": state["day"],
            "turn": turn,
            "balance": balance,
            "mode": mode,
            "panel_title": panel_title,
            "planning_available": False,
            "plan_submitted": False,
            "max_decision_actions": 3,
            "turn_plan": None,
            "free_action_candidates": [],
            "decision_action_candidates": [],
            "day_end_action_candidates": day_end_action_candidates,
            "total_cost_must_not_exceed_balance": True,
            "primary_action": None,
        }

    # Turn 2~5
    if plan_submitted:
        return {
            "success": True,
            "day": state["day"],
            "turn": turn,
            "mode": "ready_to_advance",
            "panel_title": "营业经营",
            "planning_available": False,
            "plan_submitted": True,
            "max_decision_actions": 3,
            "turn_plan": _get_turn_plan_summary(eng),
            "free_action_candidates": [],
            "decision_action_candidates": [],
            "primary_action": {
                "action": "advance_turn",
                "label": "推进经营轮次",
                "enabled": True,
                "reason": "",
            },
        }

    # Turn 2~5 尚未提交计划：读取中性候选，再补充人类展示字段。
    neutral = _build_neutral_turn_action_candidates(eng)
    free_action_candidates = []
    for source in neutral["free_action_candidates"]:
        item = dict(source)
        item.update({"label": "清洁待清洁帐篷", "category": "cleaning"})
        item.pop("cost_decision_points", None)
        free_action_candidates.append(item)
    decision_action_candidates = []
    labels = {
        "improve_service": "提升服务", "clean_campsite": "清洁营地",
        "make_post": "发布帖子", "campfire": "举行篝火",
        "stargazing": "观赏星空", "repair_tent": "维修帐篷",
        "buy_food_package": "补充食材",
    }
    categories = {
        "improve_service": "service", "clean_campsite": "cleaning",
        "make_post": "post", "campfire": "campfire",
        "stargazing": "stargazing", "repair_tent": "repair",
        "buy_food_package": "food",
    }
    for source in neutral["decision_action_candidates"]:
        item = dict(source)
        action = item["action"]
        item.update({"label": labels[action], "category": categories[action]})
        if action == "repair_tent":
            item["label"] = f"维修{item['params']['tent_id']}号帐篷"
        if action == "buy_food_package":
            package = CampingPlazaEngine.FOOD_PACKAGES[item["params"]["package_key"]]
            item["label"] = f"补充{package['name']}"
            item["detail"] = f"{package['portions']}份 · {package['price']}金币"
        item.pop("cost_decision_points", None)
        item.pop("remaining_today", None)
        decision_action_candidates.append(item)

    temporary_event = _get_temporary_event_summary(eng)

    return {
        "success": True,
        "day": state["day"],
        "turn": turn,
        "mode": "planning",
        "panel_title": "营业经营",
        "planning_available": True,
        "plan_submitted": False,
        "max_decision_actions": eng.state.decisions_left,
        "turn_plan": None,
        "free_action_candidates": free_action_candidates,
        "decision_action_candidates": decision_action_candidates,
        "temporary_event": temporary_event,
        "primary_action": {
            "action": "submit_turn_plan",
            "label": "提交本轮计划",
            "enabled": True,
            "reason": "",
        },
    }


def _get_hot_spring_status(eng: CampingPlazaEngine) -> dict:
    """温泉当前营业状态（只读），供各状态输出统一追加。"""
    served = eng.state.hot_spring_people_served_today
    return {
        "built": eng.state.hot_spring_built,
        "people_served_today": served,
        "remaining_capacity_today": eng.HOT_SPRING_DAILY_CAPACITY - served,
        "today_income": eng.state.today_income["hot_spring"],
    }


def _get_day_campsite_status(eng: CampingPlazaEngine) -> dict:
    """日间营位当天容量状态（只读），供各状态输出统一追加。"""
    return {
        "group_capacity_per_day": eng.DAY_CAMPSITE_CAPACITY,
        "groups_served_today": eng.state.day_campsite_groups_served,
        "remaining_groups_today": eng.get_day_campsite_remaining(),
    }


def _get_arrival_plan_summary(eng: CampingPlazaEngine) -> dict:
    """今日到达计划安全摘要（只读），仅聚合不生成/不修改计划。"""
    today = eng.state.day
    current_entries = [
        e for e in eng.state.today_arrival_plan
        if e.get("planned_day") == today
    ]

    total_groups = len(current_entries)
    total_people = sum(e.get("group_size", 0) for e in current_entries)
    pending_entries = [e for e in current_entries if e.get("arrival_status") == "pending"]
    arrived_groups = sum(1 for e in current_entries if e.get("arrival_status") == "arrived")
    turned_away_full_groups = sum(
        1 for e in current_entries if e.get("arrival_status") == "turned_away_full"
    )
    reservation_day_groups = sum(1 for e in current_entries if e.get("source") == "reservation" and e.get("visit_type") == "day")
    reservation_overnight_groups = sum(1 for e in current_entries if e.get("source") == "reservation" and e.get("visit_type") == "overnight")

    pending_by_turn = {
        "2": {"day_groups": 0, "day_people": 0, "overnight_groups": 0, "overnight_people": 0},
        "3": {"day_groups": 0, "day_people": 0, "overnight_groups": 0, "overnight_people": 0},
        "4": {"day_groups": 0, "day_people": 0, "overnight_groups": 0, "overnight_people": 0},
    }
    for e in pending_entries:
        turn_key = str(e.get("arrival_turn"))
        if turn_key not in pending_by_turn:
            continue
        size = e.get("group_size", 0)
        bucket = pending_by_turn[turn_key]
        if e.get("visit_type") == "overnight":
            bucket["overnight_groups"] += 1
            bucket["overnight_people"] += size
        else:
            bucket["day_groups"] += 1
            bucket["day_people"] += size

    return {
        "day": eng.state.today_arrival_plan_day,
        "total_groups": total_groups,
        "total_people": total_people,
        "pending_groups": len(pending_entries),
        "pending_people": sum(e.get("group_size", 0) for e in pending_entries),
        "arrived_groups": arrived_groups,
        "turned_away_full_groups": turned_away_full_groups,
        "reservation_day_groups": reservation_day_groups,
        "reservation_overnight_groups": reservation_overnight_groups,
        "pending_by_turn": pending_by_turn,
    }


# =============================================================================
# 游戏状态接口
# =============================================================================

@app.get("/")
def root():
    """网页入口：返回营地总览页面"""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/api/health")
def health():
    """服务状态检查"""
    return {"game": "露营广场", "version": "0.1.0", "status": "running"}


@app.get("/api/state")
def get_state():
    """获取完整游戏状态（给MCP用）"""
    eng = get_engine()
    state = eng.get_full_state()
    state["debt_remaining"] = eng.state.debt_remaining
    state["hot_spring"] = _get_hot_spring_status(eng)
    state["day_campsite"] = _get_day_campsite_status(eng)
    state["arrival_plan"] = _get_arrival_plan_summary(eng)
    state["today_events"] = list(eng.state.today_events)
    state["event_history"] = list(eng.state.event_history)
    state["review_history"] = list(eng.state.review_history)
    state["previous_day_summary"] = (
        dict(eng.state.previous_day_summary)
        if eng.state.previous_day_summary is not None
        else None
    )
    return state


@app.get("/api/growth")
def get_growth():
    """获取成长进度和成长项目目录。"""
    eng = get_engine()
    return {
        "success": True,
        "progress": eng.get_growth_progress(),
        "projects": eng.get_growth_project_catalog(),
    }


def _build_turn_action_candidates(eng: CampingPlazaEngine) -> dict:
    """将中性 Turn Plan 候选转换为 MCP 使用的机器侧结构。"""
    catalog = _build_neutral_turn_action_candidates(eng)
    candidates = {}
    for key in ("free_action_candidates", "decision_action_candidates"):
        normalized = []
        for source in catalog[key]:
            item = {
                "action": source["action"],
                "kind": source["kind"],
                "enabled": source["enabled"],
                "reason": source.get("reason", ""),
                "params": dict(source.get("params") or {}),
                "repeatable": source.get("repeatable", False),
                "cost_decision_points": 0 if source["kind"] == "free" else 1,
            }
            for field in ("remaining_today", "price", "portions", "max_quantity"):
                if field in source:
                    item[field] = source[field]
            normalized.append(item)
        candidates[key] = normalized
    return candidates


def _get_temporary_event_summary(eng: CampingPlazaEngine) -> Optional[dict]:
    """临时事件的玩家/AI 共用安全摘要，不暴露已预生成结果。"""
    event = eng.get_current_temporary_conflict_event()
    if event is None:
        return None
    return {
        "description": (
            f"{eng._visible_guest_label(event['npc_a_id'])}与"
            f"{eng._visible_guest_label(event['npc_b_id'])}发生了争执。"
        ),
        "choices": [
            {
                "value": "mediate", "label": "调解", "decision_cost": 1,
                "effect": "降低双方不满风险",
            },
            {
                "value": "ignore", "label": "不调解", "decision_cost": 0,
                "effect": "双方更可能产生不满",
            },
        ],
    }


@app.get("/mcp/query_growth_projects")
def mcp_query_growth_projects():
    """MCP 只读查询：复用现有成长目录和进度读取逻辑。"""
    return get_growth()


@app.get("/mcp/query_debt")
def mcp_query_debt():
    """MCP 只读查询：返回当前启动债务事实。"""
    return get_engine().get_debt_summary()


@app.get("/api/actions")
def get_human_actions():
    """人类网页专用只读动作目录。不执行操作，不修改存档。"""
    eng = get_engine()
    return _build_human_action_catalog(eng)


@app.get("/api/state/display")
def get_display_state():
    """获取展示用文本状态（给围观前端用）"""
    eng = get_engine()
    state = eng.get_full_state()
    state["hot_spring"] = _get_hot_spring_status(eng)
    state["day_campsite"] = _get_day_campsite_status(eng)
    state["arrival_plan"] = _get_arrival_plan_summary(eng)
    state["today_events"] = list(eng.state.today_events)
    return {
        "text": eng.get_state_for_display(),
        "data": state
    }


@app.get("/api/map")
def get_map_data():
    """获取地图数据（帐篷位置、设施位置、NPC位置）"""
    eng = get_engine()
    state = eng.get_full_state()

    # 地图坐标（相对于600x800画布）
    map_positions = {
        "tents": {
            "1": {"x": 120, "y": 150, "capacity": 2},
            "2": {"x": 280, "y": 100, "capacity": 2},
            "3": {"x": 450, "y": 180, "capacity": 3},
            "4": {"x": 150, "y": 380, "capacity": 3},
            "5": {"x": 350, "y": 320, "capacity": 4},
            "6": {"x": 480, "y": 450, "capacity": 5}
        },
        "hot_spring": {"x": 80, "y": 250},
        "dining": {"x": 300, "y": 500},
        "entertainment_a": {"x": 180, "y": 600},
        "entertainment_b": {"x": 420, "y": 600},
        "gate": {"x": 300, "y": 720}
    }

    # NPC位置映射
    npc_positions = []
    for npc in state["active_npcs"]:
        pos = {"id": npc["id"], "group_size": npc["group_size"]}

        if npc["location"].startswith("tent_"):
            tent_id = npc["location"].split("_")[1]
            tent_pos = map_positions["tents"].get(tent_id, {"x": 300, "y": 400})
            pos["x"] = tent_pos["x"] + 15
            pos["y"] = tent_pos["y"] + 15
        elif npc["location"] == "dining":
            pos["x"] = map_positions["dining"]["x"] + 15
            pos["y"] = map_positions["dining"]["y"] + 15
        elif npc["location"] == "entertainment":
            pos["x"] = map_positions["entertainment_a"]["x"] + 15
            pos["y"] = map_positions["entertainment_a"]["y"] + 15
        elif npc["location"] == "gate":
            pos["x"] = map_positions["gate"]["x"]
            pos["y"] = map_positions["gate"]["y"]
        else:
            pos["x"] = 300
            pos["y"] = 400

        npc_positions.append(pos)

    return {
        "positions": map_positions,
        "npcs": npc_positions,
        "day": state["day"],
        "turn": state["turn"]
    }


# =============================================================================
# 游戏操作接口
# =============================================================================

@app.post("/api/turn/advance")
def advance_turn():
    """推进回合"""
    eng = get_engine()
    result = eng.advance_turn()
    # 写操作后统一保存（含故障阻塞早退补足决策点等分支）
    eng.save_state()
    return result


@app.post("/api/turn/plan")
def submit_turn_plan(req: TurnPlanRequest):
    """提交本轮营业计划"""
    eng = get_engine()
    plan_result = eng.submit_turn_plan(
        _normalize_turn_plan_actions(req.free_actions),
        _normalize_turn_plan_actions(req.actions),
        req.conflict_choice,
    )
    if not plan_result["success"]:
        eng.save_state()
        return {
            "success": False,
            "message": plan_result.get("message", ""),
            "target_day": plan_result.get("target_day"),
            "target_turn": plan_result.get("target_turn"),
            "free_action_count": plan_result.get("free_actions_count", len(req.free_actions)),
            "action_count": plan_result.get("actions_count", len(req.actions)),
        }

    advance_result = eng.advance_turn()
    eng.save_state()
    response = {
        "success": True,
        "day": advance_result["day"],
        "turn": advance_result["turn"],
        "events": advance_result.get("events", []),
    }
    action_failures = [
        {
            "action": item.get("action"),
            "message": item.get("message", ""),
        }
        for action_group in advance_result.get("plan_execution", {}).values()
        for item in action_group
        if not item.get("success")
    ]
    if action_failures:
        response["action_failures"] = action_failures
    return response


@app.post("/api/day/end")
def submit_day_end(req: DayEndRequest):
    """日终批处理入口：提交完整日终经营清单并开启新一天。

    单个动作业务失败保留在 results 中，整体正常返回 200。
    """
    eng = get_engine()
    result = eng.submit_day_end_actions(_normalize_day_end_actions(req.day_end_actions))
    if result.get("success"):
        next_day_result = eng.start_next_day()
        result["events"].extend(next_day_result.get("events", []))
        result["day"] = next_day_result.get("day", eng.state.day)
        result["turn"] = next_day_result.get("turn", eng.state.turn)
        result["day_end_completed"] = eng.state.day_end_completed
    eng.save_state()
    return result


@app.post("/api/day/start")
def start_next_day():
    """日终清单完成后开启下一天（确定性跨日推进）。"""
    eng = get_engine()
    result = eng.start_next_day()
    eng.save_state()
    return result


@app.post("/api/action")
def do_action(req: ActionRequest):
    """执行经营操作"""
    eng = get_engine()

    # Turn 6 日终阶段：禁止逐项直接调用，统一走 /api/day/end 批处理
    _TURN6_DAY_END_ONLY = {
        "repair_tent",
        "clean_tents",
        "manage_greenery",
        "buy_food_package",
        "purchase_growth_project",
        "new_day",
    }
    if eng.state.turn == 6 and req.action in _TURN6_DAY_END_ONLY:
        _raise_action_request_error(
            "day_end_batch_required",
            f"Turn 6 日终阶段请使用 /api/day/end 统一提交经营清单，不再支持逐项调用 {req.action}",
        )

    if req.action == "repay_debt":
        if eng.state.turn != 6 or eng.state.day_end_completed:
            _raise_action_request_error(
                "repayment_turn_not_allowed",
                "主动偿还启动负债仅能在 Turn 6 日终决策完成前进行",
            )
        amount = (req.params or {}).get("amount")
        result = eng.repay_debt(amount)
        eng.save_state()
        return result

    if req.action in TURN_PLAN_IMMEDIATE_ACTIONS and eng.state.turn <= 5:
        result = {
            "success": False,
            "message": "请通过 /api/turn/plan 安排本轮行动。"
        }
        eng.save_state()
        return result

    if req.action == "resolve_temporary_conflict":
        choice = req.params.get("choice") if req.params else None
        if not isinstance(choice, str):
            _raise_action_request_error("missing_conflict_choice", "缺少临时事件处理方式")
        result = eng.resolve_current_temporary_conflict(choice)

    elif req.action == "repair_tent":
        tent_id = req.params.get("tent_id") if req.params else None
        if tent_id is None:
            _raise_action_request_error("missing_tent_id", "缺少tent_id参数")
        result = eng.repair_tent(int(tent_id))

    elif req.action == "manage_greenery":
        action = req.params.get("action", "skip") if req.params else "skip"
        message = eng.manage_greenery(action)
        # 修复：统一返回 success/message 结构
        failure_messages = [
            "本回合已经结算，请进入下一回合",
            "绿化管理只能在日终管理阶段（Turn 6）进行",
            "今天已经处理过绿化了"
        ]
        result = {"success": message not in failure_messages, "message": message}

    elif req.action == "improve_service":
        result = eng.improve_service()

    elif req.action == "clean_tents":
        tent_ids = None
        if req.params:
            raw_ids = req.params.get("tent_ids")
            if raw_ids is not None:
                tent_ids = [int(tid) for tid in raw_ids]
        result = eng.clean_tents(tent_ids)

    elif req.action == "buy_food_package":
        package_key = req.params.get("package_key") if req.params else None
        if package_key is None:
            _raise_action_request_error("missing_package_key", "缺少package_key参数")
        result = eng.buy_food_package(str(package_key))

    elif req.action == "purchase_growth_project":
        project_id = req.params.get("project_id") if req.params else None
        if not isinstance(project_id, str) or not project_id.strip():
            _raise_action_request_error("invalid_project_id", "缺少有效的project_id参数")
        result = eng.purchase_growth_project(project_id)
        if not result.get("success"):
            return result

    elif req.action == "advance_turn":
        result = eng.advance_turn()

    elif req.action == "new_day":
        # 日终结束，推进到新的一天
        result = eng.advance_turn()  # 这会推进到 day+1

    else:
        _raise_action_request_error("unknown_action", f"未知操作: {req.action}")

    # 写操作完成后统一保存（不按 success 过滤：失败操作也可能改变状态，
    # 如容量不足写入抱怨事件、故障阻塞补足决策点等）
    eng.save_state()
    return result


# =============================================================================
# MCP 专用接口
# =============================================================================

@app.get("/mcp/state")
def mcp_state():
    """
    MCP接口：返回AI需要的精简状态
    AI不需要知道NPC隐藏标签、后台概率等
    """
    eng = get_engine()
    state = eng.get_full_state()
    planning_available, plan_submitted, plan_target_turn = _get_turn_plan_status(eng)

    # 只返回AI决策需要的信息
    return {
        "day": state["day"],
        "turn": state["turn"],
        "balance": state["balance"],
        "average_rating": state["average_rating"],
        "decisions_left": state["decisions_left"],
        "food_stock": state["food_stock"],
        "tents": {
            tid: {
                "status": t["status"],
                "unlocked": t["unlocked"],
                "capacity": t["capacity"]
            }
            for tid, t in state["tents"].items()
        },
        "active_guests_count": len(state["active_npcs"]),
        "facilities": {
            k: {"level": v["level"]}
            for k, v in state["facilities"].items()
        },
        "reservations": state["reservations"],
        "today_income": state["today_income"],
        "hot_spring": _get_hot_spring_status(eng),
        "day_campsite": _get_day_campsite_status(eng),
        "arrival_plan": _get_arrival_plan_summary(eng),
        "greenery": state["greenery"],
        "planning_available": planning_available,
        "plan_submitted": plan_submitted,
        "plan_target_turn": plan_target_turn,
        "turn_plan": _get_turn_plan_summary(eng),
        "next_turn_checkout_tents": eng.get_next_turn_checkout_tents(),
        "day_end_completed": eng.state.day_end_completed,
        "today_events": list(eng.state.today_events),
    }


@app.get("/mcp/actions")
def mcp_available_actions():
    """
    MCP接口：返回当前可用操作
    """
    eng = get_engine()
    state = eng.get_full_state()
    actions = []
    next_calls = []
    planning_available, plan_submitted, _plan_target_turn = _get_turn_plan_status(eng)

    # 存在待清洁帐篷时提供批量清洁操作（营业和日终阶段均可）
    cleaning_tent_ids = [
        int(tid) for tid, t in state["tents"].items()
        if t["unlocked"] and t["status"] == "cleaning"
    ]
    if cleaning_tent_ids:
        actions.append({
            "action": "clean_tents",
            "params": {"tent_ids": cleaning_tent_ids},
            "description": "批量清洁待清洁帐篷（不消耗决策点）"
        })

    if state["turn"] <= 5:
        actions = []
        if state["turn"] == 1:
            actions.append({
                "action": "advance_turn",
                "description": "完成晨间结算并进入营业",
            })
        elif planning_available:
            submit_entry = {
                "action": "submit_turn_plan",
                "params": {"free_actions": [], "actions": []},
                "description": _food_package_plan_description(),
            }
            turn_candidates = _build_turn_action_candidates(eng)
            submit_entry.update(turn_candidates)
            submit_entry["max_decision_actions"] = eng.state.decisions_left
            broken_candidates = [
                {
                    "action": item["action"],
                    "params": dict(item["params"]),
                    "cost": item.get("price", CampingPlazaEngine.REPAIR_COST),
                    "enabled": item["enabled"],
                    "reason": item["reason"],
                    "description": f"维修{item['params']['tent_id']}号帐篷（{item.get('price', CampingPlazaEngine.REPAIR_COST)}金币）",
                }
                for item in turn_candidates["decision_action_candidates"]
                if item["action"] == "repair_tent"
            ]
            if broken_candidates:
                submit_entry["repair_candidates"] = broken_candidates
            temporary_event = _get_temporary_event_summary(eng)
            if temporary_event is not None:
                actions.append({
                    "action": "resolve_temporary_conflict",
                    "params": {"choice": None},
                    "required_params": [{
                        "name": "choice",
                        "type": "string",
                        "required": True,
                        "enum": ["mediate", "ignore"],
                    }],
                    "choices": ["mediate", "ignore"],
                    "choice_details": temporary_event["choices"],
                    "temporary_event": temporary_event,
                    "description": "先立即处理临时事件，再提交本轮经营计划。",
                })
            else:
                actions.append(submit_entry)
        if plan_submitted:
            actions.append({
                "action": "advance_turn",
                "description": "执行已提交的本轮计划并推进回合"
            })
    else:
        # Turn 6 日终批处理模式
        if not eng.state.day_end_completed:
            actions.append({
                "action": "repay_debt",
                "params": {"amount": None},
                "required_params": [{
                    "name": "amount",
                    "type": "integer",
                    "required": True,
                }],
                "description": (
                    "主动偿还启动负债；仅 Turn 6 日终决策完成前可用，"
                    "金额须为正整数且不超过当前余额或剩余债务；"
                    "不占经营决策位，启动负债无利息。"
                ),
            })
            next_calls.append({"action": "query_growth_projects"})
        if eng.state.day_end_completed:
            # 正常日终已由 /api/day/end 直接跨日；这里只保留异常/恢复状态，
            # 不把兼容入口包装成 AI 的正式经营动作。
            actions = []
        else:
            entry = {
                "action": "submit_day_end_actions",
                "params": {"day_end_actions": []},
                "description": "提交日终经营清单（维修、清洁、绿化、食材、成长购买等，数量不限）",
            }
            # 紧凑候选信息，复用现有生成逻辑
            broken_candidates = [
                {
                    "action": "repair_tent",
                    "params": {"tent_id": int(tid)},
                    "description": f"维修{tid}号帐篷（{CampingPlazaEngine.REPAIR_COST}金币）",
                }
                for tid, t in state["tents"].items()
                if t["unlocked"] and t["status"] == "broken"
            ]
            if broken_candidates:
                entry["repair_candidates"] = broken_candidates
            cleaning_tids = [
                int(tid) for tid, t in state["tents"].items()
                if t["unlocked"] and t["status"] == "cleaning"
            ]
            if cleaning_tids:
                entry["clean_candidates"] = [{
                    "action": "clean_tents",
                    "params": {"tent_ids": cleaning_tids},
                    "description": "批量清洁待清洁帐篷（不消耗决策点）",
                }]
            greenery_value = state.get("greenery", {})
            if (
                not eng.state.greenery_processed_today
                and greenery_value.get("value", 0) > 0
            ):
                entry["greenery_candidate"] = {
                    "action": "manage_greenery",
                    "params": {"action": "maintain"},
                    "cost": 50,
                    "description": "打理绿化",
                }
            growth_candidates = [
                {
                    "action": "purchase_growth_project",
                    "params": {"project_id": project["project_id"]},
                    "cost": project["price"],
                    "description": f"购买{project['display_name']}（{project['price']}金币）",
                }
                for project in eng.get_growth_project_catalog()
                if project.get("can_purchase_now")
            ]
            if growth_candidates:
                entry["growth_candidates"] = growth_candidates
            if eng.state.last_food_preorder_day != eng.state.day:
                entry["food_package_candidates"] = _food_package_action_entries()
            actions = [
                {
                    "action": "repay_debt",
                    "params": {"amount": None},
                    "required_params": [{
                        "name": "amount",
                        "type": "integer",
                        "required": True,
                    }],
                    "description": (
                        "主动偿还启动负债；仅 Turn 6 日终决策完成前可用，"
                        "金额须为正整数且不超过当前余额或剩余债务；"
                        "不占经营决策位，启动负债无利息。"
                    ),
                },
                entry,
            ]

    return {
        "balance": eng.state.balance,
        "day_end_completed": eng.state.day_end_completed,
        "total_cost_must_not_exceed_balance": True,
        "available_actions": actions,
        "next_calls": next_calls,
    }


# =============================================================================
# 静态资源挂载（放在路由注册之后，避免覆盖 API 路径）
# =============================================================================
app.mount("/styles", StaticFiles(directory=os.path.join(FRONTEND_DIR, "styles")), name="styles")
app.mount("/scripts", StaticFiles(directory=os.path.join(FRONTEND_DIR, "scripts")), name="scripts")
app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")


# =============================================================================
# 启动
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
