"""
露营广场 - API 接口层
提供给合集站MCP封装 + 前端状态查询
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import uvicorn

from game_engine import CampingPlazaEngine


app = FastAPI(title="露营广场 API", version="0.1.0")

# 正式存档路径固定在本文件所在目录，不依赖启动时当前工作目录
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camping_plaza.db")

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


class ReservationRequest(BaseModel):
    group_size: int
    economic_level: Optional[int] = 1


class FacilityUpgradeRequest(BaseModel):
    facility_name: str


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
        "提交下一营业Turn计划（free_actions支持clean_tents，"
        "actions支持repair_tent、improve_service、buy_food_package，"
        "buy_food_package使用package_key，"
        f"可选包：{package_text}，actions最多3项）"
    )


def _normalize_turn_plan_actions(actions: list[ActionRequest]) -> list[dict]:
    normalized = []
    for item in actions:
        params = item.params or {}
        if not isinstance(params, dict):
            raise HTTPException(400, "params必须为对象")
        normalized.append({"action": item.action, **params})
    return normalized


def _get_turn_plan_status(eng: CampingPlazaEngine) -> tuple[bool, bool, Optional[int]]:
    plan = eng.state.pending_turn_plan
    has_current_plan = bool(
        plan and (plan.get("target_day"), plan.get("target_turn")) == (eng.state.day, eng.state.turn)
    )
    plan_target_turn = plan.get("target_turn") if has_current_plan else None
    planning_available = eng.state.turn in (2, 3, 4, 5) and not has_current_plan
    return planning_available, has_current_plan, plan_target_turn


# =============================================================================
# 游戏状态接口
# =============================================================================

@app.get("/")
def root():
    return {"game": "露营广场", "version": "0.1.0", "status": "running"}


@app.get("/api/state")
def get_state():
    """获取完整游戏状态（给MCP用）"""
    eng = get_engine()
    return eng.get_full_state()


@app.get("/api/state/display")
def get_display_state():
    """获取展示用文本状态（给围观前端用）"""
    eng = get_engine()
    return {
        "text": eng.get_state_for_display(),
        "data": eng.get_full_state()
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
    """提交下一营业Turn行动计划"""
    eng = get_engine()
    result = eng.submit_turn_plan(
        _normalize_turn_plan_actions(req.free_actions),
        _normalize_turn_plan_actions(req.actions),
    )
    eng.save_state()
    return {
        "success": result["success"],
        "message": result.get("message", ""),
        "target_day": result.get("target_day"),
        "target_turn": result.get("target_turn"),
        "free_action_count": result.get("free_actions_count", len(req.free_actions)),
        "action_count": result.get("actions_count", len(req.actions)),
    }


@app.post("/api/action")
def do_action(req: ActionRequest):
    """执行经营操作"""
    eng = get_engine()

    if req.action in TURN_PLAN_IMMEDIATE_ACTIONS and eng.state.turn <= 5:
        result = {
            "success": False,
            "message": "请通过 /api/turn/plan 安排下一营业Turn行动。"
        }
        eng.save_state()
        return result

    if req.action == "repair_tent":
        tent_id = req.params.get("tent_id") if req.params else None
        if tent_id is None:
            raise HTTPException(400, "缺少tent_id参数")
        result = eng.repair_tent(int(tent_id))

    elif req.action == "upgrade_tent":
        tent_id = req.params.get("tent_id") if req.params else None
        if tent_id is None:
            raise HTTPException(400, "缺少tent_id参数")
        result = eng.upgrade_tent(int(tent_id))

    elif req.action == "upgrade_facility":
        if not req.params or "facility_name" not in req.params:
            raise HTTPException(400, "缺少facility_name参数")
        result = eng.upgrade_facility(req.params["facility_name"])

    elif req.action == "manage_greenery":
        action = req.params.get("action", "skip") if req.params else "skip"
        message = eng.manage_greenery(action)
        # 修复：统一返回 success/message 结构
        failure_messages = [
            "本回合已经结算，请进入下一回合",
            "绿化管理只能在日终管理阶段（Turn 6）进行",
            "存在故障帐篷，必须先完成维修",
            "今天已经处理过绿化了"
        ]
        result = {"success": message not in failure_messages, "message": message}

    elif req.action == "accept_reservation":
        if not req.params or "group_size" not in req.params:
            raise HTTPException(400, "缺少group_size参数")
        result = eng.accept_reservation(int(req.params["group_size"]))

    elif req.action == "reject_reservation":
        result = eng.reject_reservation()

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
            raise HTTPException(400, "缺少package_key参数")
        result = eng.buy_food_package(str(package_key))

    elif req.action == "advance_turn":
        result = eng.advance_turn()

    elif req.action == "new_day":
        # 日终结束，推进到新的一天
        result = eng.advance_turn()  # 这会推进到 day+1

    else:
        raise HTTPException(400, f"未知操作: {req.action}")

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
        "reputation_rate": state["reputation_rate"],
        "decisions_left": state["decisions_left"],
        "food_stock": state["food_stock"],
        "tents": {
            tid: {
                "status": t["status"],
                "unlocked": t["unlocked"],
                "level": t["level"],
                "capacity": t["capacity"]
            }
            for tid, t in state["tents"].items()
        },
        "active_guests_count": len(state["active_npcs"]),
        "facilities": {
            k: {"level": v["level"]}
            for k, v in state["facilities"].items()
        },
        "reservation": state["reservation"],
        "today_income": state["today_income"],
        "planning_available": planning_available,
        "plan_submitted": plan_submitted,
        "plan_target_turn": plan_target_turn,
        "next_turn_checkout_tents": eng.get_next_turn_checkout_tents(),
    }


@app.get("/mcp/actions")
def mcp_available_actions():
    """
    MCP接口：返回当前可用操作
    """
    eng = get_engine()
    state = eng.get_full_state()
    actions = []
    planning_available, plan_submitted, _plan_target_turn = _get_turn_plan_status(eng)

    # 修复：已结算回合只返回 advance_turn
    if eng.state.turn_settled:
        return {
            "available_actions": [{
                "action": "advance_turn",
                "description": "本回合已结算，进入下一回合"
            }]
        }

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
        if planning_available:
            actions.append({
                "action": "submit_turn_plan",
                "params": {"free_actions": [], "actions": []},
                "description": _food_package_plan_description()
            })
        if plan_submitted:
            actions.append({
                "action": "advance_turn",
                "description": "执行已提交的下一营业Turn计划并推进回合"
            })

        # 修复：仅 pending 状态的预定显示接受/拒绝，accepted 不显示
        if state["reservation"] and state["reservation"]["status"] == "pending":
            group_size = state["reservation"]["group_size"]
            # 修复：能否接受只检查是否存在容量足够的帐篷，不要求当前为 available
            has_capacity = any(
                t["unlocked"] and t["capacity"] >= group_size
                for t in state["tents"].values()
            )
            if has_capacity:
                actions.append({
                    "action": "accept_reservation",
                    "params": {"group_size": group_size},
                    "description": f"接受预定（{group_size}人）"
                })
            actions.append({
                "action": "reject_reservation",
                "description": "拒绝预定"
            })

    else:
        actions = []
        for tid, t in state["tents"].items():
            if t["unlocked"] and t["status"] == "broken":
                actions.append({
                    "action": "repair_tent",
                    "params": {"tent_id": int(tid)},
                    "description": f"维修{tid}号帐篷"
                })
        if cleaning_tent_ids:
            actions.append({
                "action": "clean_tents",
                "params": {"tent_ids": cleaning_tent_ids},
                "description": "批量清洁待清洁帐篷（不消耗决策点）"
            })
        # 日终管理：为每顶可升级帐篷和每个可升级设施提供带完整 params 的操作
        for tid, t in state["tents"].items():
            if t["unlocked"] and t["level"] < 3:
                actions.append({
                    "action": "upgrade_tent",
                    "params": {"tent_id": int(tid)},
                    "description": f"升级{tid}号帐篷到Lv.{t['level']+1}"
                })

        for name, f in state["facilities"].items():
            # 绿化最高 Lv2，其他设施最高 Lv3
            max_level = 2 if name == "greenery" else 3
            if f["level"] < max_level:
                actions.append({
                    "action": "upgrade_facility",
                    "params": {"facility_name": name},
                    "description": f"升级{name}到Lv.{f['level']+1}"
                })

        actions.append({
            "action": "manage_greenery",
            "params": {"action": "maintain"},
            "description": "打理绿化"
        })
        if eng.state.last_food_preorder_day != eng.state.day:
            actions.extend(_food_package_action_entries())
        actions.append({"action": "new_day", "description": "结束今天，开始新一天"})

    return {"available_actions": actions}


# =============================================================================
# 启动
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
