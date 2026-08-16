"""
露营广场 - API 接口层
提供给合集站MCP封装 + 前端状态查询
"""

import os
import re
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
import uvicorn

from game_engine import CampingPlazaEngine


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


def _resolve_database_path() -> str:
    """允许部署环境将 SQLite 存档放到持久磁盘，默认仍使用项目内路径。"""
    configured_path = os.environ.get("CAMPING_PLAZA_DB_PATH")
    if configured_path:
        return os.path.abspath(os.path.expanduser(configured_path))
    return os.path.join(BASE_DIR, "camping_plaza.db")


app = FastAPI(title="露营广场 API", version="0.1.0")

# 正式存档路径可通过环境变量配置；默认不依赖启动时当前工作目录。
DB_PATH = _resolve_database_path()

# CORS 配置（允许合集站前端访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 仅保留给既有直接函数测试注入；正式请求每次按 session_id 从持久化快照恢复。
engine: Optional[CampingPlazaEngine] = None
SESSION_ID_RE = re.compile(r"^sess_[0-9a-f]{32}$")
CHINESE_PLAYER_NAME_RE = re.compile(r"^[\u4e00-\u9fff]{2,3}$")
ASCII_PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9]{2,6}$")


def _raise_session_error(error_code: str, message: str, status_code: int = 400):
    raise HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )


def _require_session_id(raw_session_id: Optional[str]) -> str:
    if not raw_session_id:
        if engine is not None:
            return "test-session"
        _raise_session_error("missing_session_id", "缺少 session_id，无法访问游戏存档。")
    session_id = str(raw_session_id).strip()
    if not SESSION_ID_RE.fullmatch(session_id):
        _raise_session_error("invalid_session_id", "session_id 格式无效。")
    return session_id


def get_engine(session_id: Optional[str] = None, *, create_new: bool = False) -> CampingPlazaEngine:
    """加载一个 session 的独立引擎；数据库快照而非进程内全局对象是真实来源。"""
    if engine is not None:
        return engine
    session_id = _require_session_id(session_id)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        database_dir = os.path.dirname(DB_PATH)
        if database_dir:
            os.makedirs(database_dir, exist_ok=True)
    try:
        return CampingPlazaEngine(
            db_path=DB_PATH,
            database_url=database_url,
            session_id=session_id,
            create_new=create_new,
        )
    except LookupError as exc:
        if str(exc) == "session_not_found":
            _raise_session_error("session_not_found", "指定的 session_id 不存在。", 404)
        raise


# =============================================================================
# 请求/响应模型
# =============================================================================

class StrictWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionRequest(StrictWriteRequest):
    action: str
    params: Optional[dict] = None
    session_id: Optional[str] = None


class TurnPlanRequest(StrictWriteRequest):
    session_id: Optional[str] = None
    free_actions: list[ActionRequest] = Field(default_factory=list)
    actions: list[ActionRequest] = Field(default_factory=list)
    conflict_choice: Optional[str] = None


class DayEndRequest(StrictWriteRequest):
    session_id: Optional[str] = None
    day_end_actions: Optional[list[ActionRequest]] = None


class SessionRequest(StrictWriteRequest):
    session_id: Optional[str] = None


class EmptyWriteRequest(StrictWriteRequest):
    pass


class SetPlayerNameRequest(StrictWriteRequest):
    session_id: Optional[str] = None
    name: str


TURN_PLAN_IMMEDIATE_ACTIONS = {
    name for name, config in CampingPlazaEngine.TURN_PLAN_ACTIONS.items()
    if config["kind"] in {"free", "decision"}
}

_DAY_END_ACTION_PARAM_TYPES = {
    "repay_debt": {"amount": "integer"},
    "clean_tents": {"tent_ids": "integer_list"},
    "repair_tent": {"tent_id": "integer"},
    "manage_greenery": {"action": "string"},
    "buy_food_package": {"package_key": "string"},
    "purchase_growth_project": {"project_id": "string"},
}
_DAY_END_REQUIRED_PARAMS = {
    "repay_debt": {"amount"},
    "repair_tent": {"tent_id"},
    "buy_food_package": {"package_key"},
    "purchase_growth_project": {"project_id"},
}
_API_ACTION_PARAM_TYPES = {
    "resolve_temporary_conflict": {"choice": "string"},
    "repair_tent": {"tent_id": "integer"},
    "manage_greenery": {"action": "string"},
    "improve_service": {},
    "clean_tents": {"tent_ids": "integer_list"},
    "buy_food_package": {"package_key": "string"},
    "purchase_growth_project": {"project_id": "string"},
    "advance_turn": {},
    "new_day": {},
}
_API_ACTION_REQUIRED_PARAMS = {
    "resolve_temporary_conflict": {"choice"},
    "repair_tent": {"tent_id"},
    "buy_food_package": {"package_key"},
    "purchase_growth_project": {"project_id"},
}


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
        f"可选包：{package_text}，actions最多3项）。决策点使用数量可以为 0–3。"
    )


def _required_day_end_param(name: str, value_type: str) -> dict:
    return {"name": name, "type": value_type, "required": True}


TURN6_DAY_END_BUDGET_HINT = (
    "提示：如选择还款，还款金额与所选经营决策项费用合计不得超过当前余额。"
)


def _build_turn6_day_end_candidates(eng: CampingPlazaEngine) -> list[dict]:
    """生成 Turn 6 共用日终候选事实，不包含网页文案或 MCP 导航结构。"""
    if eng.state.turn != 6 or eng.state.day_end_completed:
        return []

    state = eng.get_full_state()
    balance = eng.state.balance
    candidates = []

    if eng.state.debt_remaining > 0:
        max_amount = min(balance, eng.state.debt_remaining)
        enabled = max_amount > 0
        candidates.append({
            "action": "repay_debt",
            "params": {"amount": None},
            "required_params": [_required_day_end_param("amount", "integer")],
            "cost": None,
            "min_amount": 1,
            "max_amount": max_amount,
            "enabled": enabled,
            "reason": "" if enabled else "金币不足",
        })

    cleaning_tent_ids = [
        int(tid) for tid, tent in state["tents"].items()
        if tent["unlocked"] and (tent["needs_cleaning"] or tent["status"] == "cleaning")
    ]
    if cleaning_tent_ids:
        candidates.append({
            "action": "clean_tents",
            "params": {"tent_ids": cleaning_tent_ids},
            "required_params": [],
            "cost": 0,
            "enabled": True,
            "reason": "",
        })

    for tid, tent in state["tents"].items():
        if tent["unlocked"] and tent["status"] == "broken":
            enabled = balance >= CampingPlazaEngine.REPAIR_COST
            candidates.append({
                "action": "repair_tent",
                "params": {"tent_id": int(tid)},
                "required_params": [_required_day_end_param("tent_id", "integer")],
                "cost": CampingPlazaEngine.REPAIR_COST,
                "enabled": enabled,
                "reason": "" if enabled else "金币不足",
            })

    greenery = state["greenery"]
    if greenery["value"] > 0:
        enabled = balance >= 50 and not eng.state.greenery_processed_today
        reason = ""
        if eng.state.greenery_processed_today:
            reason = "今天已经处理过绿化"
        elif balance < 50:
            reason = "金币不足"
        candidates.append({
            "action": "manage_greenery",
            "params": {"action": "maintain"},
            "required_params": [],
            "cost": 50,
            "enabled": enabled,
            "reason": reason,
        })

    if eng.state.last_food_preorder_day != eng.state.day:
        for package_key, package in CampingPlazaEngine.FOOD_PACKAGES.items():
            enabled = balance >= package["price"]
            candidates.append({
                "action": "buy_food_package",
                "params": {"package_key": package_key},
                "required_params": [_required_day_end_param("package_key", "string")],
                "cost": package["price"],
                "portions": package["portions"],
                "enabled": enabled,
                "reason": "" if enabled else "金币不足",
            })

    for project in eng.get_growth_project_catalog():
        if project.get("can_purchase_now"):
            candidates.append({
                "action": "purchase_growth_project",
                "params": {"project_id": project["project_id"]},
                "required_params": [_required_day_end_param("project_id", "string")],
                "cost": project["price"],
                "enabled": True,
                "reason": "",
            })
    return candidates


def _build_human_turn6_day_end_candidates(eng: CampingPlazaEngine) -> list[dict]:
    """为共用日终候选补充网页展示文案，不重新判断资格或可用性。"""
    candidates = []
    for source in _build_turn6_day_end_candidates(eng):
        item = dict(source)
        action = item["action"]
        params = item["params"]
        if action == "repay_debt":
            item["label"] = "偿还债务"
        elif action == "clean_tents":
            item["label"] = "清洁待清洁帐篷"
        elif action == "repair_tent":
            item["label"] = f"维修{params['tent_id']}号帐篷"
        elif action == "manage_greenery":
            item["label"] = "打理绿化"
        elif action == "buy_food_package":
            package = CampingPlazaEngine.FOOD_PACKAGES[params["package_key"]]
            item["label"] = f"补充{package['name']}"
        else:
            project = next(
                project for project in eng.get_growth_project_catalog()
                if project["project_id"] == params["project_id"]
            )
            item["label"] = f"购买{project['display_name']}"
        candidates.append(item)
    return candidates


def _raise_action_request_error(error_code: str, message: str):
    """POST /api/action 的请求语义错误：400 + 机器可读 error_code + 中文 message"""
    raise HTTPException(
        status_code=400,
        detail={
            "error_code": error_code,
            "message": message,
        },
    )


def _is_valid_action_param_type(value, value_type: str) -> bool:
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "integer_list":
        return (
            isinstance(value, list)
            and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        )
    if value_type == "string":
        return isinstance(value, str)
    return False


def _validate_action_params(
    action: str,
    params: Optional[dict],
    param_types: dict[str, dict[str, str]],
    required_params: dict[str, set[str]],
    *,
    unknown_action_error: str,
) -> dict:
    if action not in param_types:
        _raise_action_request_error(unknown_action_error, f"未知操作: {action}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        _raise_action_request_error("invalid_action_params", f"{action} 的 params 必须是对象")

    if action == "purchase_growth_project" and unknown_action_error == "unknown_action":
        project_id = params.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            _raise_action_request_error("invalid_project_id", "缺少有效的project_id参数")

    allowed_params = set(param_types[action])
    unknown_params = sorted(set(params) - allowed_params)
    if unknown_params:
        _raise_action_request_error(
            "unknown_action_param",
            f"{action} 包含未知参数: {unknown_params[0]}",
        )
    missing_params = sorted(required_params.get(action, set()) - set(params))
    if missing_params:
        _raise_action_request_error(
            "missing_action_param",
            f"{action} 缺少参数: {missing_params[0]}",
        )
    for name, value_type in param_types[action].items():
        if name in params and not _is_valid_action_param_type(params[name], value_type):
            _raise_action_request_error(
                "invalid_action_param",
                f"{action} 的参数 {name} 类型无效",
            )
    return params


def _validate_turn_plan_actions(
    actions: list[ActionRequest], expected_kind: str
) -> list[dict]:
    normalized = []
    param_types = {
        "clean_tents": {"tent_ids": "integer_list"},
        "repair_tent": {"tent_id": "integer"},
        "buy_food_package": {"package_key": "string"},
    }
    for item in actions:
        if item.session_id is not None:
            _raise_action_request_error(
                "invalid_nested_action_field", "Turn Plan 动作不能包含 session_id"
            )
        config = CampingPlazaEngine.TURN_PLAN_ACTIONS.get(item.action)
        if config is None:
            _raise_action_request_error("unknown_turn_plan_action", f"未知计划操作: {item.action}")
        if config["kind"] != expected_kind:
            _raise_action_request_error(
                "invalid_turn_plan_action_kind", f"{item.action} 不能用于该行动列表"
            )
        params = _validate_action_params(
            item.action,
            item.params,
            {item.action: param_types.get(item.action, {})},
            {item.action: set(config["required"])},
            unknown_action_error="unknown_turn_plan_action",
        )
        normalized.append({"action": item.action, **params})
    return normalized


def _validate_day_end_actions(actions: list[ActionRequest]) -> list[dict]:
    normalized = []
    for item in actions:
        if item.session_id is not None:
            _raise_action_request_error(
                "invalid_nested_action_field", "日终动作不能包含 session_id"
            )
        params = _validate_action_params(
            item.action,
            item.params,
            _DAY_END_ACTION_PARAM_TYPES,
            _DAY_END_REQUIRED_PARAMS,
            unknown_action_error="unknown_day_end_action",
        )
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
        "remaining_today": improve_remaining, "daily_limit": 2,
        "cost_decision_points": 1,
    })
    clean_remaining = max(0, 2 - eng.state.clean_campsite_uses_today)
    decision_candidates.append({
        "action": "clean_campsite", "kind": "decision",
        "enabled": clean_remaining > 0,
        "reason": "" if clean_remaining else "今日清洁营地次数已达到上限",
        "params": {}, "repeatable": False,
        "remaining_today": clean_remaining, "daily_limit": 2,
        "cost_decision_points": 1,
    })
    post_remaining = 0 if eng.state.post_used_today else 1
    decision_candidates.append({
        "action": "make_post", "kind": "decision",
        "enabled": post_remaining > 0,
        "reason": "" if post_remaining else "今天已经发布过帖子",
        "params": {}, "repeatable": False,
        "remaining_today": post_remaining, "daily_limit": 1,
        "cost_decision_points": 1,
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
        response = {
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
        return response

    # Turn 6：日终阶段，本轮只返回阶段标识
    if turn == 6:
        if day_end_completed:
            mode = "day_end_completed"
            panel_title = "日终管理"
            day_end_action_candidates = []
        else:
            mode = "day_end_pending"
            panel_title = "日终管理"
            day_end_action_candidates = _build_human_turn6_day_end_candidates(eng)
        response = {
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
            "day_end_budget_hint": TURN6_DAY_END_BUDGET_HINT,
            "primary_action": None,
        }
        if not day_end_completed:
            response["decision_summary"] = eng.get_turn6_decision_summary()
        return response

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


@app.post("/api/session")
def create_session(req: Optional[EmptyWriteRequest] = None):
    """创建一份独立的新游戏存档。"""
    session_id = f"sess_{uuid.uuid4().hex}"
    get_engine(session_id, create_new=True)
    return {
        "success": True,
        "session_id": session_id,
    }


@app.get("/api/state")
def get_state(session_id: Optional[str] = None):
    """获取完整游戏状态（给MCP用）"""
    eng = get_engine(session_id)
    state = eng.get_full_state()
    state["player_name"] = eng.state.player_name
    state.pop("achievements", None)
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
def get_growth(session_id: Optional[str] = None):
    """获取成长进度和成长项目目录。"""
    eng = get_engine(session_id)
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
            for field in (
                "remaining_today", "daily_limit", "price", "portions", "max_quantity"
            ):
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
def mcp_query_growth_projects(session_id: Optional[str] = None):
    """MCP 只读查询：复用现有成长目录和进度读取逻辑。"""
    return get_growth(session_id)


@app.get("/mcp/query_debt")
def mcp_query_debt(session_id: Optional[str] = None):
    """MCP 只读查询：返回当前启动债务事实。"""
    return get_engine(session_id).get_debt_summary()


@app.get("/api/achievements")
def get_achievements(session_id: Optional[str] = None):
    """人类图鉴主动查询；不进入常规状态返回。"""
    return get_engine(session_id).get_achievement_catalog()


@app.get("/mcp/achievements")
def mcp_get_achievements(session_id: Optional[str] = None):
    """MCP 主动查询成就图鉴；不进入每 Turn 的常规状态。"""
    return get_engine(session_id).get_achievement_catalog()


@app.get("/api/actions")
def get_human_actions(session_id: Optional[str] = None):
    """人类网页专用只读动作目录。不执行操作，不修改存档。"""
    eng = get_engine(session_id)
    catalog = _build_human_action_catalog(eng)
    catalog["achievement_unlocked_count"] = eng.get_achievement_catalog()["unlocked_count"]
    return catalog


# =============================================================================
# 游戏操作接口
# =============================================================================

def _require_onboarding_complete(eng: CampingPlazaEngine) -> None:
    """阻止绕过 MCP 动作目录直接开始新存档的经营操作。"""
    if eng.state.player_name is None and engine is None:
        _raise_action_request_error("onboarding_required", "请先设置玩家名称。")

@app.post("/api/turn/advance")
def advance_turn(req: Optional[SessionRequest] = None):
    """推进回合"""
    eng = get_engine(req.session_id if req is not None else None)
    _require_onboarding_complete(eng)
    result = eng.advance_turn()
    # 写操作后统一保存（含故障阻塞早退补足决策点等分支）
    eng.save_state()
    return result


@app.post("/api/turn/plan")
def submit_turn_plan(req: TurnPlanRequest):
    """提交本轮营业计划"""
    free_actions = _validate_turn_plan_actions(req.free_actions, "free")
    actions = _validate_turn_plan_actions(req.actions, "decision")
    eng = get_engine(req.session_id)
    _require_onboarding_complete(eng)
    plan_result = eng.submit_turn_plan(
        free_actions,
        actions,
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
    """日终批处理入口：提交完整日终经营清单，等待确认后再开启新一天。

    单个动作业务失败保留在 results 中；success 只表示日终清单已处理完成，
    action_execution_status、succeeded_count 和 failed_count 表示动作汇总结果。
    """
    if req.day_end_actions is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "missing_day_end_actions",
                "message": (
                    "缺少 day_end_actions；请使用 day_end_actions: [] "
                    "显式提交空日终清单。"
                ),
            },
        )
    day_end_actions = _validate_day_end_actions(req.day_end_actions)
    eng = get_engine(req.session_id)
    _require_onboarding_complete(eng)
    result = eng.submit_day_end_actions(day_end_actions)
    if result.get("success"):
        result["day"] = eng.state.day
        result["turn"] = eng.state.turn
        result["day_end_completed"] = eng.state.day_end_completed
        result["balance"] = eng.state.balance
        result["next_action"] = "start_next_day"
        result["next_endpoint"] = "/api/day/start"
    eng.save_state()
    return result


@app.post("/api/day/start")
def start_next_day(req: Optional[SessionRequest] = None):
    """日终清单完成后开启下一天（确定性跨日推进）。"""
    eng = get_engine(req.session_id if req is not None else None)
    _require_onboarding_complete(eng)
    result = eng.start_next_day()
    eng.save_state()
    return result


@app.post("/api/action")
def do_action(req: ActionRequest):
    """执行经营操作"""
    eng = get_engine(req.session_id)
    _require_onboarding_complete(eng)

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

    params = _validate_action_params(
        req.action,
        req.params,
        _API_ACTION_PARAM_TYPES,
        _API_ACTION_REQUIRED_PARAMS,
        unknown_action_error="unknown_action",
    )

    if req.action in TURN_PLAN_IMMEDIATE_ACTIONS and eng.state.turn <= 5:
        result = {
            "success": False,
            "message": "请通过 /api/turn/plan 安排本轮行动。"
        }
        eng.save_state()
        return result

    if req.action == "resolve_temporary_conflict":
        result = eng.resolve_current_temporary_conflict(params["choice"])

    elif req.action == "repair_tent":
        result = eng.repair_tent(params["tent_id"])

    elif req.action == "manage_greenery":
        action = params.get("action", "skip")
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
        result = eng.clean_tents(params.get("tent_ids"))

    elif req.action == "buy_food_package":
        result = eng.buy_food_package(params["package_key"])

    elif req.action == "purchase_growth_project":
        project_id = params["project_id"]
        if not project_id.strip():
            _raise_action_request_error("invalid_action_param", "project_id 不能为空")
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


def _is_valid_player_name(name: str) -> bool:
    return bool(
        CHINESE_PLAYER_NAME_RE.fullmatch(name)
        or ASCII_PLAYER_NAME_RE.fullmatch(name)
    )


@app.post("/api/player/name")
def set_player_name(req: SetPlayerNameRequest):
    """完成新存档的首次取名；正常游戏中不提供改名入口。"""
    eng = get_engine(req.session_id)
    if eng.state.player_name is not None:
        _raise_action_request_error("player_name_already_set", "玩家名称已设置，不能修改。")
    if not _is_valid_player_name(req.name):
        _raise_action_request_error(
            "invalid_player_name",
            "名称须为 2-3 个汉字，或 2-6 个英文字母或数字。",
        )

    eng.state.player_name = req.name
    if not eng.save_state():
        eng.state.player_name = None
        _raise_session_error("save_failed", "玩家名称保存失败，请稍后重试。", 500)
    return {
        "success": True,
        "message": f"欢迎你，{req.name}。",
        "state": mcp_state(req.session_id),
    }


# =============================================================================
# MCP 专用接口
# =============================================================================

@app.get("/mcp/state")
def mcp_state(session_id: Optional[str] = None):
    """
    MCP接口：返回AI需要的精简状态
    AI不需要知道NPC隐藏标签、后台概率等
    """
    eng = get_engine(session_id)
    # 注入 engine 仅用于既有直接函数测试；真实 HTTP session 一律走首次取名流程。
    if eng.state.player_name is None and engine is None:
        return {
            "onboarding": {
                "game": "露营广场",
                "message": "欢迎来到《露营广场》！你将接手一座刚刚起步的露营地。为了让营地能够顺利开始营业，这里已经完成了一些基础设施建设，因此还有 6,000 金币的建设费用需要偿还。请在第 25 天结束前还清。开门营业前，先告诉我你的名字吧。",
                "name_rules": "中文名限 2-3 个汉字；英文名限 2-6 个字母或数字，不使用空格、标点或特殊符号。",
            }
        }
    state = eng.get_full_state()
    planning_available, plan_submitted, plan_target_turn = _get_turn_plan_status(eng)

    # 只返回AI决策需要的信息
    response = {
        "day": state["day"],
        "turn": state["turn"],
        "balance": state["balance"],
        "average_rating": state["average_rating"],
        "tents": {
            tid: {
                "status": t["status"],
                "needs_cleaning": t["needs_cleaning"],
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
        "greenery": state["greenery"],
    }
    if state["turn"] in (2, 3, 4, 5):
        response.update({
            "decisions_left": state["decisions_left"],
            "planning_available": planning_available,
            "plan_submitted": plan_submitted,
        })
        if plan_target_turn is not None:
            response["plan_target_turn"] = plan_target_turn
        if plan_target_turn is not None:
            turn_plan = _get_turn_plan_summary(eng)
            if turn_plan is not None:
                response["turn_plan"] = turn_plan

    arrival_plan = _get_arrival_plan_summary(eng)
    if (
        arrival_plan["total_groups"]
        or arrival_plan["pending_groups"]
        or arrival_plan["arrived_groups"]
        or arrival_plan["turned_away_full_groups"]
        or arrival_plan["reservation_day_groups"]
        or arrival_plan["reservation_overnight_groups"]
    ):
        response["arrival_plan"] = arrival_plan

    if state["turn"] != 6:
        response["food_stock"] = state["food_stock"]
        response["today_income"] = state["today_income"]

    hot_spring = _get_hot_spring_status(eng)
    if (
        hot_spring["built"]
        or hot_spring["people_served_today"]
        or hot_spring["today_income"]
    ):
        response["hot_spring"] = hot_spring

    day_campsite = _get_day_campsite_status(eng)
    if (
        day_campsite["groups_served_today"]
        or day_campsite["remaining_groups_today"] != day_campsite["group_capacity_per_day"]
    ):
        response["day_campsite"] = day_campsite

    next_turn_checkout_tents = eng.get_next_turn_checkout_tents()
    if next_turn_checkout_tents:
        response["next_turn_checkout_tents"] = next_turn_checkout_tents

    if state["turn"] == 6:
        response["day_end_completed"] = eng.state.day_end_completed

    if eng.state.today_events:
        response["today_events"] = list(eng.state.today_events)

    waiting_tent_ids = eng.get_waiting_cleaning_checkin_tent_ids()
    if waiting_tent_ids:
        response["turn_alerts"] = [
            "有客人正在等待入住，请及时清洁待清洁帐篷。"
        ]
    return response


@app.get("/mcp/actions")
def mcp_available_actions(session_id: Optional[str] = None):
    """
    MCP接口：返回当前可用操作
    """
    eng = get_engine(session_id)
    # 注入 engine 仅用于既有直接函数测试；真实 HTTP session 一律走首次取名流程。
    if eng.state.player_name is None and engine is None:
        return {
            "available_actions": [{
                "action": "set_player_name",
                "endpoint": "/api/player/name",
                "params": {"name": ""},
                "required_params": [{
                    "name": "name",
                    "type": "string",
                    "required": True,
                }],
                "description": "设置本存档的经营者名称。",
            }],
            "available_queries": [],
        }
    state = eng.get_full_state()
    actions = []
    planning_available, plan_submitted, _plan_target_turn = _get_turn_plan_status(eng)

    if state["turn"] <= 5:
        actions = []
        if state["turn"] == 1:
            actions.append({
                "action": "advance_turn",
                "description": "完成晨间结算并进入营业",
            })
        elif planning_available:
            submit_entry = {
                "action": "execute_turn_plan",
                "params": {"free_actions": [], "actions": []},
                "endpoint": "/api/turn/plan",
                "description": "提交并执行当前回合完整计划，执行完成后推进到下一回合。" + _food_package_plan_description(),
            }
            turn_candidates = _build_turn_action_candidates(eng)
            submit_entry.update(turn_candidates)
            submit_entry["max_decision_actions"] = eng.state.decisions_left
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
        if eng.state.day_end_completed:
            actions = [{
                "action": "start_next_day",
                "endpoint": "/api/day/start",
                "description": "确认进入新的一天。",
            }]
        else:
            entry = {
                "action": "submit_day_end_actions",
                "params": {"day_end_actions": []},
                "endpoint": "/api/day/end",
                "description": "提交日终经营清单；可同时提交多项，提交后停留在 Turn 6 等待确认进入新一天。",
                "day_end_action_candidates": _build_turn6_day_end_candidates(eng),
            }
            actions = [entry]

    response = {
        "balance": eng.state.balance,
        "day_end_completed": eng.state.day_end_completed,
        "total_cost_must_not_exceed_balance": True,
        "day_end_budget_hint": (
            TURN6_DAY_END_BUDGET_HINT if state["turn"] == 6 else None
        ),
        "available_actions": actions,
        "available_queries": [
            {
                "name": "查看设施升级详情",
                "endpoint": "/mcp/query_growth_projects",
            },
            {
                "name": "查看成就图鉴",
                "endpoint": "/mcp/achievements",
            },
        ],
    }
    if state["turn"] == 6 and not eng.state.day_end_completed:
        response["decision_summary"] = eng.get_turn6_decision_summary()
    return response


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
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
