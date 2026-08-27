"""
露营广场 - 游戏核心引擎
类型：MCP 经营游戏，AI负责经营，人类围观
设计版本：v0.3
"""

import os
import math
import random
import json
import sqlite3
from typing import Optional
from dataclasses import dataclass, field, asdict

try:
    import psycopg2
except ImportError:
    psycopg2 = None


# =============================================================================
# 数据结构
# =============================================================================

@dataclass
class Tent:
    """帐篷"""
    id: int
    capacity: int
    is_unlocked: bool = False
    status: str = "available"  # available, occupied, cleaning, broken, reserved
    needs_cleaning: bool = False
    occupied_by: Optional[int] = None  # NPC组ID
    next_breakdown_turn: int = 0
    # 本次故障的及时维修窗口；仅保存故障链路本身，不参与客组计划生成。
    breakdown_repair_state: Optional[dict] = None

    CAPACITY_MAP = {1: 2, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}


@dataclass
class NPCGroup:
    """NPC客人组"""
    id: int
    group_size: int
    visit_type: str  # "day" | "overnight"
    arrival_turn: int = 0
    location: str = "gate"  # gate, campsite, tent_1-6, dining, entertainment, leaving
    campsite_slot: Optional[int] = None
    total_satisfaction: int = 60
    # 本次到访中实际生效的满意度变化账本；不把初始值或存档恢复计入体验。
    positive_experience_total: float = 0.0
    negative_experience_total: float = 0.0
    has_left: bool = False
    review_left: bool = False
    review_rating: int = 0
    review_attempted: bool = False

    # 隐藏标签
    economic_level: int = 0  # 0-2: 低/中/高
    spending_habit: int = 0  # 0-2: 吝啬/普通/大方
    temperament: int = 0  # 0-2: 温和/普通/暴躁

    last_dining_day: int = 0
    checkout_turn: Optional[int] = None

    # 预定标记
    is_reserved: bool = False  # 是否是预定客
    paid: bool = False  # 是否已付款
    greenery_entry_bonus_applied: bool = False
    broken_tent_penalty: int = 0
    had_food_shortage: bool = False
    had_tent_problem: bool = False
    received_service_boost: bool = False

    # 成长进度账本的幂等标记：同一到访客组只计入一次。
    growth_served_recorded: bool = False
    growth_dining_recorded: bool = False
    growth_paid_entertainment_recorded: bool = False


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
    # None 表示新存档尚未完成首次取名；它也是唯一的 onboarding 完成标志。
    player_name: Optional[str] = None
    day: int = 1
    turn: int = 1  # 1-5 营业回合, 6 日终管理
    balance: int = 1000
    initial_debt: int = 21000
    debt_remaining: int = 21000
    repayment_deadline_day: int = 26
    # Day 26 晨间启动资金结算的唯一完成标记；不得用余额或事件文本推断。
    startup_debt_settlement_completed: bool = False
    day_start_balance: Optional[int] = None
    previous_day_summary: Optional[dict] = None
    total_reviews: int = 0
    total_rating_sum: int = 0
    pending_reviews: list = field(default_factory=list)
    review_history: list = field(default_factory=list)
    campsite_star: int = 1
    historical_highest_rating: Optional[float] = None
    pending_turn_plan: Optional[dict] = None

    today_income: dict = field(default_factory=lambda: {
        "accommodation": 0,
        "campsite": 0,
        "dining": 0,
        "entertainment": 0,
        "hot_spring": 0,
        "nature_observation": 0,
        "tip": 0,
    })
    today_expenses: dict = field(default_factory=lambda: {
        "food": 0,
        "greenery": 0,
        "repair": 0,
        "conflict_care": 0,
        "growth": 0,
        "lodging_consumables": 0,
        "hot_spring_operating": 0,
    })
    post_used_today: bool = False
    pending_post_reservation: Optional[dict] = None
    campfire_affected_npc_ids: list = field(default_factory=list)
    stargazing_affected_npc_ids: list = field(default_factory=list)
    today_tip_settled: bool = False
    today_events: list = field(default_factory=list)
    event_history: list = field(default_factory=list)
    event_sequence: int = 0
    decisions_left: int = 5
    improve_service_uses_today: int = 0
    clean_campsite_uses_today: int = 0
    day_campsite_groups_served: int = 0
    food_stock: int = 0
    last_food_preorder_day: int = 0
    hot_spring_built: bool = False
    hot_spring_people_served_today: int = 0
    nature_observation_station_built: bool = False
    nature_observation_intro_seen: bool = False
    discovered_insects: list[str] = field(default_factory=list)

    # 长期成长进度账本。旧快照缺少这些字段时保持默认 0，不补算历史。
    total_served_groups: int = 0
    successful_dining_groups: int = 0
    successful_paid_entertainment_groups: int = 0
    successful_greenery_maintenance_count: int = 0
    unlocked_achievement_ids: list[str] = field(default_factory=list)
    pending_achievement_ids: list[str] = field(default_factory=list)

    # 尚未转换进当天 arrival plan 的成功预约
    reservations: list[dict] = field(default_factory=list)

    # 绿化每日标记
    greenery_processed_today: bool = False

    # 修复：营业回合结算后产生故障，标记本回合结算已完成

    # 日终批处理完成标记：Turn 6 清单执行后设为 True，开启下一天后重置
    day_end_completed: bool = False

    daily_demand_profile_day: int = 0
    daily_demand_profile: Optional[dict] = None

    today_arrival_plan_day: int = 0
    today_arrival_plan: list = field(default_factory=list)
    today_conflict_event: Optional[dict] = None


# =============================================================================
# 游戏引擎
# =============================================================================

class CampingPlazaEngine:
    """露营广场游戏引擎"""

    DAY_CAMPSITE_CAPACITY = 10
    DAILY_DECISION_LIMIT = 5
    MAX_TURN_PLAN_DECISION_ACTIONS = 3
    EVENT_HISTORY_LIMIT = 100
    GUEST_MOMENT_CHANCE = 0.45
    DAY_TO_OVERNIGHT_INTENT_PROBABILITY = 0.15
    TENT_PRICES = {1: 160, 2: 160, 3: 230, 4: 310, 5: 400, 6: 500}
    CAMPSITE_FEE = 70
    REPAIR_COST = 100
    TENT_BREAKDOWN_SATISFACTION_PENALTY = 10
    DINING_BASE_PRICE = 30
    DINING_PLANNED_ACTION_PROBABILITIES = {0: 0.40, 1: 0.55, 2: 0.70}
    PAID_ENTERTAINMENT_PLANNED_ACTION_PROBABILITIES = {0: 0.30, 1: 0.50, 2: 0.70}
    FREE_ENTERTAINMENT_PLANNED_ACTION_PROBABILITY = 0.50
    HOT_SPRING_PLANNED_ACTION_PROBABILITY = 0.30
    HOT_SPRING_PRICE_PER_PERSON = 80
    HOT_SPRING_SATISFACTION_GAIN = 6
    HOT_SPRING_DAILY_CAPACITY = 20
    TEMPORARY_CONFLICT_EVENT_PROBABILITY = 0.25
    TEMPORARY_CONFLICT_SAME_TEMPERAMENT_MULTIPLIER = 0.95
    TEMPORARY_CONFLICT_DIFFERENT_TEMPERAMENT_MULTIPLIER = 1.05
    TEMPORARY_CONFLICT_SATISFACTION_PENALTY = 2
    TEMPORARY_CONFLICT_PENALTY_PROBABILITIES = {
        "verbal": {0: 0.10, 1: 0.30, 2: 0.55},
        "gift": {0: 0.05, 1: 0.15, 2: 0.30},
    }
    TEMPORARY_CONFLICT_GIFT_COST = 40
    TEMPORARY_CONFLICT_TOPIC_TEMPLATES = {
        "quiet_hours_noise": (
            "因为休息时段说话声太大拌了几句嘴",
            "因为休息时段的声音有些吵闹起了争执",
        ),
        "shared_area": (
            "因为公共区域怎么使用拌了几句嘴",
            "因为公共区域的使用起了争执",
        ),
        "campsite_boundary": (
            "因为营位边界或物品摆放起了争执",
            "因为营位边界和物品摆放闹得有些不愉快",
        ),
        "facility_queue": (
            "因为公共设施的使用顺序闹得有些不愉快",
            "因为公共设施前的先后顺序起了争执",
        ),
        "activity_disturbance": (
            "因为活动时不小心打扰了邻近客人起了争执",
            "因为活动动静打扰到邻近客人拌了几句嘴",
        ),
        "passage_blocked": (
            "因为通行空间被临时占住起了争执",
            "因为临时占住了通行空间闹得有些不愉快",
        ),
    }
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
    DINING_SET_MENU_WEIGHTS = {
        0: {"basic": 60, "standard": 30, "premium": 10},
        1: {"basic": 30, "standard": 50, "premium": 20},
        2: {"basic": 20, "standard": 30, "premium": 50},
    }
    ENTERTAINMENT_TIER_OPTIONS = {
        "basic": {
            "display_name": "桌游箱租赁",
            "price_per_group": 40,
            "satisfaction_gain": 2,
        },
        "standard": {
            "display_name": "射箭体验",
            "price_per_group": 60,
            "satisfaction_gain": 4,
        },
        "premium": {
            "display_name": "便携 K 歌设备租赁",
            "price_per_group": 90,
            "satisfaction_gain": 6,
        },
    }
    FREE_ENTERTAINMENT_NAMES_BY_LEVEL = {
        0: ("飞盘", "纸牌"),
        1: ("飞盘", "纸牌", "投壶"),
        2: ("飞盘", "纸牌", "投壶", "露天电影"),
    }
    FOOD_PACKAGES = {
        "small": {"name": "小包", "portions": 4, "price": 80},
        "medium": {"name": "中包", "portions": 8, "price": 150},
        "large": {"name": "大包", "portions": 14, "price": 250},
    }
    OPENING_FOOD_GIFT_PACKAGE = "medium"

    GREENERY_LEVEL_MAX = {0: 4.0, 1: 7.0, 2: 10.0}
    ACHIEVEMENT_CATALOG = {
        "first_served_group": {
            "title": "真来人了",
            "hint": "营地开始运转。",
            "condition": "首次成功接待一组客人。",
        },
        "first_day_complete": {
            "title": "老板上线",
            "hint": "欢迎光临！",
            "condition": "Day 1 正式结束并进入 Day 2。",
        },
        "first_overnight_group": {
            "title": "今晚住这儿",
            "hint": "有人愿意把一晚留在营地。",
            "condition": "首次成功接待一组过夜客。",
        },
        "first_day_to_overnight": {
            "title": "不着急走",
            "hint": "一次比原计划更久的停留。",
            "condition": "首次有日间客成功转为过夜客。",
        },
        "first_tip": {
            "title": "还有小费！",
            "hint": "客人离开时多留下的一点心意。",
            "condition": "首次收到客人小费。",
        },
        "tent_2_purchased": {
            "title": "地盘 +1",
            "hint": "开启新空间。",
            "condition": "购买 2 号帐篷。",
        },
        "all_tents_unlocked": {
            "title": "都住得下",
            "hint": "空间全开。",
            "condition": "解锁全部 6 顶帐篷。",
        },
        "dining_lv1": {
            "title": "先吃饭吧",
            "hint": "美食more more。",
            "condition": "首次升级餐饮至 Lv1。",
        },
        "entertainment_lv1": {
            "title": "有得玩了",
            "hint": "丰富一下娱乐活动。",
            "condition": "首次升级娱乐至 Lv1。",
        },
        "greenery_lv1": {
            "title": "有点绿了",
            "hint": "营地第一次有了更像样的绿意。",
            "condition": "首次升级绿化至 Lv1。",
        },
        "all_normal_growth_complete": {
            "title": "差不多齐活",
            "hint": "营地的常规建设逐渐接近完整。",
            "condition": "完成温泉之前全部 11 个普通成长节点。",
        },
        "hot_spring_built": {
            "title": "开泡",
            "hint": "营地迎来一项更大的设施。",
            "condition": "建成温泉。",
        },
        "served_groups_50": {
            "title": "客人来了",
            "hint": "营地渐渐有了稳定的人气。",
            "condition": "累计成功接待 50 组客人。",
        },
        "served_groups_100": {
            "title": "越来越热闹",
            "hint": "这份人气继续往上积累。",
            "condition": "累计成功接待 100 组客人。",
        },
        "served_groups_150": {
            "title": "生意兴隆",
            "hint": "营地真正热闹起来以后。",
            "condition": "累计成功接待 150 组客人。",
        },
        "first_insect_discovered": {
            "title": "草丛来客",
            "hint": "草丛里，好像有点动静。",
            "condition": "首次发现一种昆虫。",
        },
        "insects_discovered_6": {
            "title": "虫脉拓宽",
            "hint": "再认识一些营地里的小邻居。",
            "condition": "累计发现 6 种不同昆虫。",
        },
        "all_insects_discovered": {
            "title": "虫口普查完成",
            "hint": "还有小家伙藏在角落里。",
            "condition": "发现全部 12 种昆虫。",
        },
        "bad_luck_breakdowns": {
            "title": "坏事成双",
            "hidden_title": "今天是不是有点太衰了？",
            "hidden_hint": "有些成就，还是别拿到比较好。",
            "condition": "同一个经营轮次内，新发生至少 2 顶帐篷故障。",
        },
        "debt_paid_by_deadline": {
            "title": "一身轻",
            "hint": "",
            "condition": "Day 26 晨间启动资金结算时，一次性结清全部剩余启动资金。",
        },
        "debt_unpaid_by_deadline": {
            "title": "没关系",
            "hint": "",
            "condition": "Day 26 晨间启动资金结算时，现有资金不足以结清全部剩余启动资金。",
        },
    }
    ACHIEVEMENT_DEFINITIONS = {
        "first_day_complete": "老板上线",
        "first_served_group": "真来人了",
        "first_overnight_group": "今晚住这儿",
        "first_day_to_overnight": "不着急走",
        "first_tip": "还有小费！",
        "tent_2_purchased": "地盘 +1",
        "all_tents_unlocked": "都住得下",
        "dining_lv1": "先吃饭吧",
        "entertainment_lv1": "有得玩了",
        "greenery_lv1": "有点绿了",
        "all_normal_growth_complete": "差不多齐活",
        "hot_spring_built": "开泡",
        "served_groups_50": "客人来了",
        "served_groups_100": "越来越热闹",
        "served_groups_150": "生意兴隆",
        "first_insect_discovered": "草丛来客",
        "insects_discovered_6": "虫脉拓宽",
        "all_insects_discovered": "虫口普查完成",
        "bad_luck_breakdowns": "坏事成双",
        "debt_paid_by_deadline": "一身轻",
        "debt_unpaid_by_deadline": "没关系",
    }
    DEBT_RESULT_ACHIEVEMENT_IDS = frozenset({
        "debt_paid_by_deadline", "debt_unpaid_by_deadline",
    })
    CAMPSITE_STAR_REQUIREMENTS = {
        2: {"served_groups": 15, "growth_nodes": 1},
        3: {"served_groups": 45, "growth_nodes": 5, "historical_rating": 4.1},
        4: {"served_groups": 90, "growth_nodes": 9, "historical_rating": 4.3},
        5: {
            "served_groups": 150,
            "growth_nodes": 10,
            "historical_rating": 4.6,
            "hot_spring_built": True,
        },
    }
    GROWTH_PROJECT_CATALOG = (
        {
            "project_id": "tent_2", "category": "tent", "display_name": "2号帐篷",
            "price": 600, "target_tent_id": 2, "sequence": 1,
            "prerequisite_tent_id": 1, "operation": "day", "required_day": 2,
        },
        {
            "project_id": "tent_3", "category": "tent", "display_name": "3号帐篷",
            "price": 1100, "target_tent_id": 3, "sequence": 2,
            "prerequisite_tent_id": 2, "operation": "campsite_star",
            "required_campsite_star": 2,
        },
        {
            "project_id": "tent_4", "category": "tent", "display_name": "4号帐篷",
            "price": 1900, "target_tent_id": 4, "sequence": 3,
            "prerequisite_tent_id": 3, "operation": "campsite_star",
            "required_campsite_star": 3,
        },
        {
            "project_id": "tent_5", "category": "tent", "display_name": "5号帐篷",
            "price": 3200, "target_tent_id": 5, "sequence": 4,
            "prerequisite_tent_id": 4, "operation": "campsite_star",
            "required_campsite_star": 4,
        },
        {
            "project_id": "tent_6", "category": "tent", "display_name": "6号帐篷",
            "price": 4800, "target_tent_id": 6, "sequence": 5,
            "prerequisite_tent_id": 5, "operation": "campsite_star",
            "required_campsite_star": 5,
        },
        {
            "project_id": "dining_lv1", "category": "dining", "display_name": "餐饮 Lv1",
            "price": 700, "target_level": 1, "sequence": 6,
            "required_level": 0, "operation": "campsite_star",
            "required_campsite_star": 2,
        },
        {
            "project_id": "dining_lv2", "category": "dining", "display_name": "餐饮 Lv2",
            "price": 1800, "target_level": 2, "sequence": 7,
            "required_level": 1, "operation": "campsite_star",
            "required_campsite_star": 3,
        },
        {
            "project_id": "entertainment_lv1", "category": "entertainment",
            "display_name": "娱乐 Lv1", "price": 600, "target_level": 1,
            "sequence": 8, "required_level": 0, "operation": "campsite_star",
            "required_campsite_star": 2,
        },
        {
            "project_id": "entertainment_lv2", "category": "entertainment",
            "display_name": "娱乐 Lv2", "price": 1600, "target_level": 2,
            "sequence": 9, "required_level": 1, "operation": "campsite_star",
            "required_campsite_star": 3,
        },
        {
            "project_id": "greenery_lv1", "category": "greenery", "display_name": "绿化 Lv1",
            "price": 600, "target_level": 1, "sequence": 10,
            "required_level": 0, "operation": "campsite_star",
            "required_campsite_star": 2,
        },
        {
            "project_id": "greenery_lv2", "category": "greenery", "display_name": "绿化 Lv2",
            "price": 1600, "target_level": 2, "sequence": 11,
            "required_level": 1, "operation": "campsite_star",
            "required_campsite_star": 3,
        },
        {
            "project_id": "hot_spring", "category": "hot_spring", "display_name": "温泉",
            "price": 3000, "sequence": 12,
            "operation": "campsite_star", "required_campsite_star": 4,
        },
        {
            "project_id": "nature_observation_station",
            "category": "nature_observation_station",
            "display_name": "自然观察站", "price": 800, "sequence": 13,
            "operation": "campsite_star", "required_campsite_star": 3,
        },
    )
    NATURE_OBSERVATION_PARTICIPATION_PERCENT = 25
    INSECT_CATALOG = (
        {"id": "ladybug", "name": "七星瓢虫", "rarity": "common", "weight": 4},
        {"id": "white_butterfly", "name": "粉蝶", "rarity": "common", "weight": 4},
        {"id": "dragonfly", "name": "蜻蜓", "rarity": "common", "weight": 4},
        {"id": "cicada", "name": "蝉", "rarity": "common", "weight": 4},
        {"id": "mantis", "name": "螳螂", "rarity": "common", "weight": 4},
        {"id": "grasshopper", "name": "蚱蜢", "rarity": "common", "weight": 4},
        {"id": "swallowtail", "name": "凤蝶", "rarity": "uncommon", "weight": 3},
        {"id": "firefly", "name": "萤火虫", "rarity": "uncommon", "weight": 3},
        {"id": "longhorn_beetle", "name": "天牛", "rarity": "uncommon", "weight": 3},
        {"id": "stick_insect", "name": "竹节虫", "rarity": "uncommon", "weight": 3},
        {"id": "rhinoceros_beetle", "name": "独角仙", "rarity": "rare", "weight": 2},
        {"id": "stag_beetle", "name": "锹形虫", "rarity": "rare", "weight": 2},
    )
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
        "clean_campsite": {"kind": "decision", "required": (), "optional": ()},
        "make_post": {"kind": "decision", "required": (), "optional": ()},
        "campfire": {"kind": "decision", "required": (), "optional": ()},
        "stargazing": {"kind": "decision", "required": (), "optional": ()},
    }

    REVIEW_COMMENT_PHRASES = {
        "dining": (
            "饭菜挺不错", "吃得挺满意", "餐饮比预想中更好",
            "用餐的时候氛围挺好", "套餐分量挺实在", "饭菜热乎，吃得挺舒坦",
            "篝火厨房的饭比想象中好吃",
        ),
        "paid_entertainment": (
            "游戏屋挺有意思", "玩得挺开心", "娱乐项目还不错",
            "玩起来挺带劲", "娱乐区的项目比预想中好玩",
            "在娱乐区玩得很尽兴", "这次娱乐体验很顺",
        ),
        "free_entertainment": (
            "游戏屋挺有意思", "玩得挺开心", "娱乐项目还不错",
            "飞盘和纸牌都挺有意思", "和大家一起玩得很开心",
            "娱乐区氛围不错", "在娱乐区玩了一会儿，感觉挺放松",
        ),
        "hot_spring": (
            "温泉泡得很舒服", "泡汤体验不错", "温泉水温正好",
            "泡完温泉整个人都松快了", "温泉区环境很舒服",
            "晚上泡温泉很解乏", "温泉比预想中舒服",
        ),
        "service_boost": (
            "工作人员挺贴心", "服务很周到", "有什么需求响应得挺快",
            "工作人员很热情", "被照顾得很周到", "服务让人感觉很舒服",
            "营地方做事挺细致",
        ),
        "greenery": (
            "营地环境挺舒服", "绿化让人很放松", "营地里绿意很足",
            "环境清清爽爽的", "绿植多，待着心情很好",
            "营地比照片里看着更舒服", "空气里都是草木的味道，挺舒服",
        ),
        "food_shortage": (
            "吃饭的时候稍微折腾了一下", "餐饮准备还有提升空间",
            "等了一会儿才吃上饭", "用餐时食材有点跟不上",
            "想吃饭的时候等了等", "餐台补货有点慢", "那天吃饭等了挺久",
        ),
        "tent_problem": (
            "帐篷出了点状况", "住宿的小问题有点影响体验",
            "帐篷有些小毛病", "夜里帐篷有点状况", "住宿时帐篷出了点问题",
            "帐篷的拉链不太好用", "帐篷有点旧，有些小问题",
        ),
        "hot_spring_full": (
            "想泡温泉的时候没排上", "温泉有点太抢手了",
            "去泡温泉的时候人满了", "没赶上温泉，有点遗憾",
            "温泉人太多没泡上", "想泡温泉得早点去，人不少",
            "温泉排队没轮上",
        ),
    }
    REVIEW_GENERIC_COMMENTS = {
        5: (
            "很满意，下次还想再来。",
            "整体体验很好，待得很舒服。",
            "各方面都很顺利，玩得很开心。",
            "营地体验超出预期，很满意。",
            "这次住得很舒服，会推荐给朋友。",
        ),
        4: (
            "整体不错，是一次挺舒服的体验。",
            "挺喜欢这里的，下次有机会还会来。",
            "体验总体不错，一些小地方可以更好。",
            "玩得挺开心，整体满意。",
            "营地氛围很好，体验不错。",
        ),
        3: (
            "整体还可以，还有一些提升空间。",
            "中规中矩，体验还算顺利。",
            "体验一般，不算差也不算特别好。",
            "还有进步空间，但整体过得去。",
            "中等的体验，期望能更好一些。",
        ),
        2: (
            "这次体验比较一般，希望之后能更完善。",
            "有些地方不太顺利，还有改进空间。",
            "体验有点失望，希望下次能更好。",
            "这次不太尽兴，有些环节需要改进。",
            "整体体验一般，有些方面没跟上。",
        ),
        1: (
            "这次体验不太理想。",
            "整体没有达到预期。",
            "这次住得不太舒服。",
            "体验比较糟糕，希望改进。",
            "不太符合预期，这次挺失望的。",
        ),
    }

    # 快照版本号，结构变更时递增
    SNAPSHOT_VERSION = 1
    STARTUP_DEBT_SETTLEMENT_DAY = 26

    def __init__(
        self,
        db_path: str = "camping_plaza.db",
        database_url: Optional[str] = None,
        session_id: str = "local-default",
        create_new: bool = True,
    ):
        self.db_path = db_path
        if database_url is None:
            database_url = os.environ.get("DATABASE_URL", "")
        self.database_url = database_url.strip()
        self.use_postgres = self.database_url.startswith(("postgres://", "postgresql://"))
        self.session_id = session_id
        self._initialize_fresh_game()
        # 持久化：数据库中的 session_id 是存档隔离边界；先读后写，绝不覆盖其他 session。
        self._ensure_snapshot_table()
        load_result = self.load_state()
        if load_result == "no_snapshot":
            if not create_new:
                raise LookupError("session_not_found")
            self._apply_opening_food_gift()
            self.save_state()
        elif load_result == "load_error":
            raise RuntimeError(
                "存档加载失败，游戏已停止启动，以避免覆盖现有存档。"
            )

        if self.state.turn == 1 and self._ensure_today_arrival_plan():
            self.save_state()

    def _initialize_fresh_game(self, player_name: Optional[str] = None) -> None:
        """建立完整的新游戏运行态；不触碰 session_id 或数据库。"""
        self.state = GameState(player_name=player_name)
        self.tents = {}
        self.npc_pool = []
        self.facilities = {}
        self._npc_id_counter = 0
        self._init_game()
        self.state.day_start_balance = self.state.balance

    def restart_game(self) -> dict:
        """在当前 session 原地重启，并保留已完成 onboarding 的玩家名称。"""
        player_name = self.state.player_name
        self._initialize_fresh_game(player_name=player_name)
        self._apply_opening_food_gift()
        self._ensure_today_arrival_plan()
        return {
            "success": True,
            "restarted": True,
            "day": self.state.day,
            "turn": self.state.turn,
            "message": "游戏已重新开始。",
        }

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
            "observation_plan": None,
            "is_reserved": npc.is_reserved,
            "paid": npc.paid,
            "tent_id": tent_id,
            "day_to_overnight_intent": (
                random.random() < self.DAY_TO_OVERNIGHT_INTENT_PROBABILITY
                if npc.visit_type == "day"
                else False
            ),
            # 过夜客（含预约过夜客与自然过夜客）的退房 Turn 在计划包生成时一次性确定。
            # 用 random.choice 而非 random.random，避免扰动现有按 random.random 序列
            # 断言计划生成的测试；日间客不消费随机数，固定为 None。
            "checkout_turn": (
                random.choice((1, 2))
                if npc.visit_type == "overnight"
                else None
            ),
        }

    def _roll_arrival_turn(self) -> int:
        return random.choice((2, 3, 4))

    def _schedule_planned_actions(
        self,
        arrival_turn: int,
        planned_actions: list[dict],
        latest_turn: int = 5,
        *,
        shuffle_actions: bool = True,
    ) -> list[dict]:
        if arrival_turn > latest_turn:
            raise ValueError("arrival_turn cannot be later than latest_turn")
        if not planned_actions:
            return planned_actions
        available_turns = list(range(arrival_turn, latest_turn + 1))
        if len(planned_actions) > len(available_turns):
            raise ValueError("planned action count exceeds available turns")

        scheduled_actions = list(planned_actions)
        if shuffle_actions:
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
            "menu_key": self._choose_weighted_unlocked_tier_key(
                self.facilities["dining"].level,
                entry["economic_level"],
                self.DINING_SET_MENU_ORDER,
                self.DINING_SET_MENU_WEIGHTS,
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
            "tier_key": self._choose_weighted_unlocked_tier_key(
                self.facilities["entertainment"].level,
                entry["economic_level"],
                self.DINING_SET_MENU_ORDER,
                self.DINING_SET_MENU_WEIGHTS,
            ),
            "status": "pending",
        }

    def _build_free_entertainment_planned_action(self) -> Optional[dict]:
        if random.random() >= self.FREE_ENTERTAINMENT_PLANNED_ACTION_PROBABILITY:
            return None
        return {
            "action": "free_entertainment",
            "status": "pending",
        }

    def _build_hot_spring_planned_action(self) -> Optional[dict]:
        if not self.state.hot_spring_built:
            return None
        if random.random() >= self.HOT_SPRING_PLANNED_ACTION_PROBABILITY:
            return None
        return {
            "action": "hot_spring",
            "status": "pending",
        }

    def _append_planned_actions(
        self, entry: dict, required_actions: Optional[list[dict]] = None
    ):
        entry["planned_actions"].clear()
        required_actions = list(required_actions or [])
        available_turn_count = 5 - entry["arrival_turn"] + 1
        if len(required_actions) > available_turn_count:
            raise ValueError("planned action count exceeds available turns")

        optional_actions = []
        dining_action = self._build_dining_planned_action(entry)
        if dining_action is not None:
            optional_actions.append(dining_action)
        paid_entertainment_action = self._build_paid_entertainment_planned_action(entry)
        if paid_entertainment_action is not None:
            optional_actions.append(paid_entertainment_action)
        free_entertainment_action = self._build_free_entertainment_planned_action()
        if free_entertainment_action is not None:
            optional_actions.append(free_entertainment_action)
        hot_spring_action = self._build_hot_spring_planned_action()
        if hot_spring_action is not None:
            optional_actions.append(hot_spring_action)

        random.shuffle(optional_actions)
        remaining_turn_count = available_turn_count - len(required_actions)
        entry["planned_actions"].extend(required_actions)
        entry["planned_actions"].extend(optional_actions[:remaining_turn_count])
        self._schedule_planned_actions(
            entry["arrival_turn"], entry["planned_actions"], shuffle_actions=False
        )

    def _normalize_discovered_insects(self, insect_ids) -> list[str]:
        """只保留正式目录中的虫种，并固定为目录顺序。"""
        if not isinstance(insect_ids, (list, tuple, set)):
            return []
        known_ids = {insect["id"] for insect in self.INSECT_CATALOG}
        discovered = {
            insect_id for insect_id in insect_ids
            if isinstance(insect_id, str) and insect_id in known_ids
        }
        return [
            insect["id"] for insect in self.INSECT_CATALOG
            if insect["id"] in discovered
        ]

    def _unlock_insect_discovery_achievements(self) -> None:
        """按已发现的不同虫种数量即时解锁图鉴成就。"""
        discovered_count = len(self._normalize_discovered_insects(
            self.state.discovered_insects
        ))
        if discovered_count >= 1:
            self._unlock_achievement("first_insect_discovered")
        if discovered_count >= 6:
            self._unlock_achievement("insects_discovered_6")
        if discovered_count >= len(self.INSECT_CATALOG):
            self._unlock_achievement("all_insects_discovered")

    def _get_nature_observation_discovery_percent(self) -> int:
        discovered_count = len(self._normalize_discovered_insects(
            self.state.discovered_insects
        ))
        if discovered_count < 3:
            return 35
        if discovered_count < 6:
            return 45
        if discovered_count < 9:
            return 55
        return 65

    def _roll_nature_observation_result(self, discovery_percent: int) -> str:
        """对未发现与全部正式虫种做一次完整的整数权重抽取。"""
        insect_weight_total = sum(insect["weight"] for insect in self.INSECT_CATALOG)
        weighted_results = [("not_found", (100 - discovery_percent) * insect_weight_total)]
        weighted_results.extend(
            (insect["id"], discovery_percent * insect["weight"])
            for insect in self.INSECT_CATALOG
        )
        total_weight = sum(weight for _, weight in weighted_results)
        roll = random.randrange(total_weight)
        for result, weight in weighted_results:
            if roll < weight:
                return result
            roll -= weight
        raise RuntimeError("nature observation weight pool is invalid")

    def _append_observation_plan(self, entry: dict, discovery_percent: int) -> None:
        """为一组客人写入独立的隐藏自然观察计划，不占普通行动槽。"""
        if random.randrange(100) >= self.NATURE_OBSERVATION_PARTICIPATION_PERCENT:
            return
        entry["observation_plan"] = {
            "planned_turn": random.choice(tuple(range(entry["arrival_turn"], 6))),
            "status": "pending",
            "result": self._roll_nature_observation_result(discovery_percent),
        }

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
        management_quality = self._calculate_management_quality()
        development_degree = self._calculate_development_degree()
        raw_demand = management_quality * development_degree * 10
        return self._probabilistic_round(raw_demand)

    def _calculate_management_quality(self) -> float:
        greenery = self.facilities["greenery"]
        return (
            self._get_average_rating_ratio()
            + (self.facilities["dining"].level + 1) / 3
            + (self.facilities["entertainment"].level + 1) / 3
            + greenery.greenery_satisfaction / 10
        ) / 4

    def get_average_rating(self) -> Optional[float]:
        """返回最近 20 条已结算评价的当前平均星级；尚无评价时返回 None。"""
        ratings = [int(review["rating"]) for review in self.state.review_history]
        if not ratings:
            return None
        return sum(ratings[-20:]) / min(len(ratings), 20)

    def get_campsite_star_progress(self) -> dict:
        """只读返回营地星级与下一星级条件进度。"""
        current_star = self.state.campsite_star
        next_star = current_star + 1 if current_star < 5 else None
        progress = {
            "campsite_star": current_star,
            "current_star": current_star,
            "historical_highest_rating": self.state.historical_highest_rating,
            "next_star": next_star,
            "is_max_star": next_star is None,
        }
        if next_star is not None:
            requirements = self.CAMPSITE_STAR_REQUIREMENTS[next_star]
            growth_progress = self.get_growth_progress()
            conditions = self._get_campsite_star_condition_progress(
                next_star, growth_progress
            )
            progress["next_star_requirements"] = dict(requirements)
            progress["conditions"] = conditions
            progress["requirement_met"] = all(
                condition["met"] for condition in conditions.values()
            )
            progress["next_star_requirement_met"] = progress["requirement_met"]
            progress["pending_morning_upgrade"] = progress["requirement_met"]
        else:
            progress["requirement_met"] = True
            progress["next_star_requirement_met"] = None
            progress["pending_morning_upgrade"] = False
        return progress

    def _get_campsite_star_condition_progress(
        self, target_star: int, growth_progress: Optional[dict] = None
    ) -> dict:
        """返回指定下一星级的逐项条件进度；条件集合来自正式星级要求。"""
        requirements = self.CAMPSITE_STAR_REQUIREMENTS[target_star]
        growth_progress = growth_progress or self.get_growth_progress()
        conditions = {
            "served_groups": {
                "current": self.state.total_served_groups,
                "required": requirements["served_groups"],
                "met": self.state.total_served_groups >= requirements["served_groups"],
            },
            "growth_nodes": {
                "current": growth_progress["completed_growth_nodes"],
                "required": requirements["growth_nodes"],
                "met": growth_progress["completed_growth_nodes"]
                >= requirements["growth_nodes"],
            },
        }
        if "historical_rating" in requirements:
            current_rating = self.state.historical_highest_rating
            required_rating = requirements["historical_rating"]
            conditions["historical_rating"] = {
                "current": current_rating,
                "required": required_rating,
                "met": current_rating is not None and current_rating >= required_rating,
            }
        if "hot_spring_built" in requirements:
            conditions["hot_spring_built"] = {
                "current": self.state.hot_spring_built,
                "required": True,
                "met": self.state.hot_spring_built,
            }
        return conditions

    def _is_campsite_star_requirement_met(
        self, target_star: int, growth_progress: Optional[dict] = None
    ) -> bool:
        conditions = self._get_campsite_star_condition_progress(
            target_star, growth_progress
        )
        return all(condition["met"] for condition in conditions.values())

    def _update_campsite_star(self) -> bool:
        """按顺序升级营地星级；已获得的星级绝不回退。"""
        upgraded = False
        while self.state.campsite_star < 5:
            target_star = self.state.campsite_star + 1
            if not self._is_campsite_star_requirement_met(target_star):
                break
            self.state.campsite_star = target_star
            upgraded = True
        return upgraded

    def _migrate_legacy_campsite_star_state(self, raw_state: dict) -> None:
        """仅为缺少星级字段的旧快照恢复可证明的最低营地星级。"""
        if "total_served_groups" not in raw_state:
            achievement_lower_bounds = {
                "served_groups_50": 50,
                "served_groups_100": 100,
                "served_groups_150": 150,
            }
            self.state.total_served_groups = max(
                (
                    served_groups
                    for achievement_id, served_groups in achievement_lower_bounds.items()
                    if achievement_id in self.state.unlocked_achievement_ids
                ),
                default=0,
            )

        if "historical_highest_rating" not in raw_state:
            try:
                current_rating = self.get_average_rating()
            except (KeyError, TypeError, ValueError):
                current_rating = None
            if current_rating is not None:
                self.state.historical_highest_rating = current_rating

        self._update_campsite_star()

    def get_debt_summary(self) -> dict:
        """返回启动负债的当前派生摘要。"""
        debt_remaining = self.state.debt_remaining
        return {
            "balance": self.state.balance,
            "initial_debt": self.state.initial_debt,
            "debt_remaining": debt_remaining,
            "debt_repaid_total": self.state.initial_debt - debt_remaining,
            "repayment_deadline_day": self.STARTUP_DEBT_SETTLEMENT_DAY,
            "days_until_deadline": max(
                0, self.STARTUP_DEBT_SETTLEMENT_DAY - self.state.day
            ),
            "automatic_settlement_completed": (
                self.state.startup_debt_settlement_completed
            ),
            "is_paid_off": debt_remaining == 0,
            "is_overdue": False,
        }

    @classmethod
    def _normalize_achievement_ids(cls, achievement_ids) -> list[str]:
        if not isinstance(achievement_ids, list):
            return []
        normalized = []
        for achievement_id in achievement_ids:
            if (
                isinstance(achievement_id, str)
                and achievement_id in cls.ACHIEVEMENT_DEFINITIONS
                and achievement_id not in normalized
            ):
                normalized.append(achievement_id)
        return normalized

    def _unlock_achievement(self, achievement_id: str) -> bool:
        if achievement_id not in self.ACHIEVEMENT_DEFINITIONS:
            raise ValueError(f"unknown achievement: {achievement_id}")
        if achievement_id in self.state.unlocked_achievement_ids:
            return False
        self.state.unlocked_achievement_ids.append(achievement_id)
        if achievement_id not in self.state.pending_achievement_ids:
            self.state.pending_achievement_ids.append(achievement_id)
        return True

    def _achievement_payload(self, achievement_ids: list[str]) -> list[dict]:
        return [
            {
                "id": achievement_id,
                "name": self.ACHIEVEMENT_DEFINITIONS[achievement_id],
            }
            for achievement_id in achievement_ids
            if achievement_id in self.ACHIEVEMENT_DEFINITIONS
        ]

    def get_achievement_state(self) -> dict:
        return {
            "unlocked": self._achievement_payload(
                self.state.unlocked_achievement_ids
            ),
            "pending": self._achievement_payload(self.state.pending_achievement_ids),
        }

    def get_achievement_catalog(self) -> dict:
        """返回图鉴展示所需的最小派生数据，不新增成就持久化状态。"""
        unlocked_ids = set(self.state.unlocked_achievement_ids)
        # 幂等标记只说明“不再自动扣款”；结果卡是否揭晓必须有真实成就事实。
        debt_result_revealed = bool(
            unlocked_ids & self.DEBT_RESULT_ACHIEVEMENT_IDS
        )
        achievements = []
        for achievement_id, definition in self.ACHIEVEMENT_CATALOG.items():
            title = definition["title"]
            if achievement_id in self.DEBT_RESULT_ACHIEVEMENT_IDS:
                if not debt_result_revealed:
                    achievements.append({
                        "id": achievement_id,
                        "title": "隐藏成就",
                        "status": "hidden",
                        "description": "",
                    })
                    continue
                status = "unlocked" if achievement_id in unlocked_ids else "alternative"
                description = definition["condition"]
            elif (
                achievement_id not in unlocked_ids
                and "hidden_title" in definition
            ):
                status = "hidden"
                description = definition["hidden_hint"]
                title = definition["hidden_title"]
            elif achievement_id in unlocked_ids:
                status = "unlocked"
                description = definition["condition"]
            else:
                status = "locked"
                description = definition["hint"]
            achievements.append({
                "id": achievement_id,
                "title": title,
                "status": status,
                "description": description,
            })
        return {
            "unlocked_count": len(unlocked_ids),
            "achievements": achievements,
        }

    def _consume_pending_achievements(self) -> list[dict]:
        pending = self._achievement_payload(self.state.pending_achievement_ids)
        self.state.pending_achievement_ids.clear()
        return pending

    def _record_growth_project_achievements(self, project_id: str) -> None:
        project_achievements = {
            "tent_2": "tent_2_purchased",
            "dining_lv1": "dining_lv1",
            "entertainment_lv1": "entertainment_lv1",
            "greenery_lv1": "greenery_lv1",
            "hot_spring": "hot_spring_built",
        }
        achievement_id = project_achievements.get(project_id)
        if achievement_id is not None:
            self._unlock_achievement(achievement_id)
        if all(self.tents[tent_id].is_unlocked for tent_id in range(1, 7)):
            self._unlock_achievement("all_tents_unlocked")
        if self.get_growth_progress()["completed_growth_nodes"] >= 11:
            self._unlock_achievement("all_normal_growth_complete")

    def repay_debt(self, amount: int) -> dict:
        """偿还无息启动负债；这是独立财务行为，不占经营决策位。"""
        if isinstance(amount, bool) or not isinstance(amount, int):
            return {"success": False, "error_code": "invalid_repayment_amount", "message": "还款金额必须是整数"}
        if amount <= 0:
            return {"success": False, "error_code": "invalid_repayment_amount", "message": "还款金额必须大于0"}
        if self.state.debt_remaining <= 0:
            return {"success": False, "error_code": "debt_already_paid_off", "message": "启动负债已还清"}
        if amount > self.state.balance:
            return {"success": False, "error_code": "repayment_exceeds_balance", "message": "还款金额不能超过当前余额"}
        if amount > self.state.debt_remaining:
            return {"success": False, "error_code": "repayment_exceeds_debt", "message": "还款金额不能超过剩余负债"}
        if (
            self.state.day < self.STARTUP_DEBT_SETTLEMENT_DAY
            or not self.state.startup_debt_settlement_completed
            or self.state.turn != 6
        ):
            return {
                "success": False,
                "error_code": "repayment_not_available",
                "message": "偿还剩余启动资金仅在 Day 26 晨间结算后、Turn 6 开放",
            }

        balance_before = self.state.balance
        debt_before = self.state.debt_remaining
        self.state.balance -= amount
        self.state.debt_remaining -= amount
        data = {
            "amount": amount,
            "balance_before": balance_before,
            "balance_after": self.state.balance,
            "debt_before": debt_before,
            "debt_after": self.state.debt_remaining,
        }
        self._record_business_event(
            self.state.day, self.state.turn, "repay_debt", data=data,
            kind="action", merge=False, actor="player", action="repay_debt",
        )
        return {
            "success": True,
            "message": f"已偿还启动负债{amount}金币",
            **data,
            "debt_repaid_total": self.state.initial_debt - self.state.debt_remaining,
        }

    def _get_average_rating_ratio(self) -> float:
        """客流计算使用平均星级比例；无评价时采用 3.0 星基准。"""
        average_rating = self.get_average_rating()
        return (average_rating if average_rating is not None else 3.0) / 5

    def _calculate_development_degree(self) -> float:
        unlocked_tent_count = sum(1 for tent in self.tents.values() if tent.is_unlocked)
        return (1 + unlocked_tent_count / 6) / 2

    def _probabilistic_round(self, value: float) -> int:
        base = math.floor(value)
        fraction = round(value - base, 10)
        if math.isclose(fraction, 0.0):
            return base
        return base + 1 if random.random() < fraction else base

    def _calculate_overnight_guest_demand(self) -> int:
        management_quality = self._calculate_management_quality()
        development_degree = self._calculate_development_degree()
        raw_demand = management_quality * development_degree * 6
        return self._probabilistic_round(raw_demand)

    def _calculate_daily_visitor_demand(self) -> dict:
        profile = self._ensure_daily_demand_profile()
        return {
            "day_guest_count": profile["natural_day_group_demand"],
            "overnight_guest_count": profile["natural_overnight_group_demand"],
        }

    def _ensure_daily_demand_profile(self) -> dict:
        if (
            self.state.daily_demand_profile_day == self.state.day
            and self.state.daily_demand_profile is not None
        ):
            return self.state.daily_demand_profile

        profile = {
            "natural_day_group_demand": self._calculate_day_guest_demand(),
            "natural_overnight_group_demand": self._calculate_overnight_guest_demand(),
            "reservations_processed": False,
        }
        self.state.daily_demand_profile = profile
        self.state.daily_demand_profile_day = self.state.day
        return profile

    def _create_reservation_record(
        self,
        *,
        group_size: int,
        visit_type: str,
        arrival_day: int,
        paid: bool,
        status: str,
    ) -> dict:
        reservation_guest = NPCGroup(
            id=self._next_npc_id(),
            group_size=group_size,
            visit_type=visit_type,
            is_reserved=True,
            paid=paid,
        )
        self._assign_hidden_tags(reservation_guest)
        return {
            "npc_id": reservation_guest.id,
            "group_size": reservation_guest.group_size,
            "visit_type": reservation_guest.visit_type,
            "arrival_day": arrival_day,
            "paid": reservation_guest.paid,
            "status": status,
            "economic_level": reservation_guest.economic_level,
            "spending_habit": reservation_guest.spending_habit,
            "temperament": reservation_guest.temperament,
            "total_satisfaction": reservation_guest.total_satisfaction,
        }

    def _find_reservable_overnight_tent(self, group_size: int) -> Optional[Tent]:
        target_day = self.state.day + 1
        reserved_tent_ids = {
            reservation.get("tent_id")
            for reservation in self.state.reservations
            if (
                reservation.get("arrival_day") == target_day
                and reservation.get("visit_type") == "overnight"
                and reservation.get("status") == "accepted"
            )
        }
        suitable_tents = [
            tent for tent in self._get_unlocked_tents()
            if tent.capacity >= group_size and tent.id not in reserved_tent_ids
        ]
        if not suitable_tents:
            return None
        return min(suitable_tents, key=lambda tent: (tent.capacity, tent.id))

    def _get_max_unlocked_tent_capacity(self) -> int:
        unlocked_tents = self._get_unlocked_tents()
        if not unlocked_tents:
            return 0
        return max(tent.capacity for tent in unlocked_tents)

    def _create_day_guest(self) -> NPCGroup:
        npc = NPCGroup(
            id=self._next_npc_id(),
            group_size=random.randint(1, 6),
            visit_type="day",
        )
        self._assign_hidden_tags(npc)
        return npc

    def _create_overnight_guest(self) -> NPCGroup:
        npc = NPCGroup(
            id=self._next_npc_id(),
            group_size=random.randint(1, 6),
            visit_type="overnight",
        )
        self._assign_hidden_tags(npc)
        return npc

    def _ensure_today_arrival_plan(self) -> bool:
        if self.state.today_arrival_plan_day == self.state.day:
            return False

        demand = self._calculate_daily_visitor_demand()
        planned_entries = []
        observation_discovery_percent = (
            self._get_nature_observation_discovery_percent()
            if self.state.nature_observation_station_built else None
        )

        remaining_reservations = []
        for reservation in self.state.reservations:
            if (
                reservation.get("status") != "accepted"
                or reservation.get("arrival_day") != self.state.day
            ):
                remaining_reservations.append(reservation)
                continue

            reserved_guest = NPCGroup(
                id=reservation["npc_id"],
                group_size=reservation["group_size"],
                visit_type=reservation["visit_type"],
                total_satisfaction=reservation.get("total_satisfaction", 60),
                is_reserved=True,
                paid=reservation.get("paid", False),
            )
            reserved_guest.economic_level = reservation.get("economic_level", 1)
            reserved_guest.spending_habit = reservation.get("spending_habit", 1)
            reserved_guest.temperament = reservation.get("temperament", 1)
            entry = self._build_arrival_plan_entry(
                reserved_guest,
                self._roll_arrival_turn(),
                "reservation",
                tent_id=reservation.get("tent_id"),
            )
            self._append_planned_actions(entry)
            if observation_discovery_percent is not None:
                self._append_observation_plan(entry, observation_discovery_percent)
            planned_entries.append(entry)
        self.state.reservations = remaining_reservations

        natural_guests = [
            self._create_day_guest()
            for _ in range(demand["day_guest_count"])
        ]
        natural_guests.extend(
            self._create_overnight_guest()
            for _ in range(demand["overnight_guest_count"])
        )

        for guest in natural_guests:
            arrival_turn = self._roll_arrival_turn()
            source = "natural_day" if guest.visit_type == "day" else "natural_overnight"
            entry = self._build_arrival_plan_entry(guest, arrival_turn, source)
            self._append_planned_actions(entry)
            if observation_discovery_percent is not None:
                self._append_observation_plan(entry, observation_discovery_percent)
            planned_entries.append(entry)

        self.state.today_arrival_plan = planned_entries
        self.state.today_arrival_plan_day = self.state.day
        self._initialize_today_conflict_event()
        return True

    def _initialize_today_conflict_event(self) -> None:
        """在当天到达计划落盘后，只生成一次临时矛盾事件。"""
        if self.state.today_conflict_event is not None:
            return
        entries = [
            entry for entry in self.state.today_arrival_plan
            if entry.get("planned_day") == self.state.day
        ]
        if len(entries) < 2:
            self.state.today_conflict_event = {"status": "no_event"}
            return
        npc_a, npc_b = random.sample(entries, 2)
        conflict_probability = self._get_temporary_conflict_probability(npc_a, npc_b)
        if random.random() >= conflict_probability:
            self.state.today_conflict_event = {"status": "no_event"}
            return
        trigger_turn = random.randint(
            max(npc_a["arrival_turn"], npc_b["arrival_turn"]), 5
        )
        self.state.today_conflict_event = {
            "status": "scheduled",
            "npc_a_id": npc_a["npc_id"],
            "npc_b_id": npc_b["npc_id"],
            "trigger_turn": trigger_turn,
            "verbal_result": self._roll_temporary_conflict_result(npc_a, npc_b, "verbal"),
            "gift_result": self._roll_temporary_conflict_result(npc_a, npc_b, "gift"),
            "topic": random.choice(tuple(self.TEMPORARY_CONFLICT_TOPIC_TEMPLATES)),
            "topic_variant": None,
        }
        topic = self.state.today_conflict_event["topic"]
        self.state.today_conflict_event["topic_variant"] = random.randrange(
            len(self.TEMPORARY_CONFLICT_TOPIC_TEMPLATES[topic])
        )

    def _get_temporary_conflict_probability(self, npc_a: dict, npc_b: dict) -> float:
        same_temperament = npc_a.get("temperament") == npc_b.get("temperament")
        multiplier = (
            self.TEMPORARY_CONFLICT_SAME_TEMPERAMENT_MULTIPLIER
            if same_temperament
            else self.TEMPORARY_CONFLICT_DIFFERENT_TEMPERAMENT_MULTIPLIER
        )
        return self.TEMPORARY_CONFLICT_EVENT_PROBABILITY * multiplier

    def _get_temporary_conflict_penalty_probability(self, npc: dict, choice: str) -> float:
        """返回该客组不接受当前处理方式的后台概率。"""
        return self.TEMPORARY_CONFLICT_PENALTY_PROBABILITIES[choice].get(
            npc.get("temperament"), 0.0
        )

    def _roll_temporary_conflict_result(self, npc_a: dict, npc_b: dict, choice: str) -> dict:
        return {
            "npc_a_delta": -self.TEMPORARY_CONFLICT_SATISFACTION_PENALTY
            if random.random() < self._get_temporary_conflict_penalty_probability(
                npc_a, choice
            ) else 0,
            "npc_b_delta": -self.TEMPORARY_CONFLICT_SATISFACTION_PENALTY
            if random.random() < self._get_temporary_conflict_penalty_probability(
                npc_b, choice
            ) else 0,
        }

    def get_current_temporary_conflict_event(self) -> Optional[dict]:
        event = self.state.today_conflict_event
        if not isinstance(event, dict) or event.get("status") != "scheduled":
            return None
        if event.get("trigger_turn") != self.state.turn:
            return None
        return event

    def _apply_temporary_conflict_event(self, result: dict) -> None:
        event = self.get_current_temporary_conflict_event()
        if event is None:
            return
        choice = result.get("conflict_choice")
        if choice not in {"verbal", "gift"}:
            raise RuntimeError("scheduled temporary conflict is missing a choice")
        outcome = event[f"{choice}_result"]
        npc_by_id = {npc.id: npc for npc in self.npc_pool}
        for key, delta_key in (("npc_a_id", "npc_a_delta"), ("npc_b_id", "npc_b_delta")):
            npc = npc_by_id.get(event[key])
            if npc is not None:
                self.apply_satisfaction_delta(npc, outcome[delta_key])
        labels = [self._visible_guest_label(event["npc_a_id"]), self._visible_guest_label(event["npc_b_id"])]
        affected_labels = [
            label for label, delta_key in zip(labels, ("npc_a_delta", "npc_b_delta"))
            if outcome[delta_key] < 0
        ]
        if choice == "verbal":
            if not affected_labels:
                outcome_text = "你进行了口头调解，双方很快平静下来。"
            elif len(affected_labels) == 1:
                outcome_text = f"你进行了口头调解，但{affected_labels[0]}仍有些不满。"
            else:
                outcome_text = "你进行了口头调解，但双方情绪都没有完全平复。"
        elif not affected_labels:
            outcome_text = "你准备了水果或小礼物安抚，双方很快平静下来。"
        elif len(affected_labels) == 1:
            outcome_text = f"你准备了水果或小礼物安抚，但{affected_labels[0]}仍有些不满。"
        else:
            outcome_text = "你准备了水果或小礼物安抚，但双方情绪都没有完全平复。"
        opening = self._format_temporary_conflict_opening(labels, event)
        message = f"临时事件：{opening}{outcome_text}"
        result["events"].append(message)
        self._record_business_event(
            self.state.day,
            event["trigger_turn"],
            "temporary_conflict",
            guest_ids=[event["npc_a_id"], event["npc_b_id"]],
            data={"choice": choice, "topic": event.get("topic"),
                "topic_variant": event.get("topic_variant"), "affected_guest_ids": [
                npc_id for npc_id, delta_key in (
                    (event["npc_a_id"], "npc_a_delta"),
                    (event["npc_b_id"], "npc_b_delta"),
                ) if outcome[delta_key] < 0
            ]},
            merge=False,
        )
        event["status"] = "resolved"

    def _format_temporary_conflict_opening(self, labels: list[str], event: dict) -> str:
        """使用冲突生成时保存的主题生成稳定起因；旧档缺主题时回退泛化文案。"""
        reason = None
        topic = event.get("topic") if isinstance(event, dict) else None
        variants = self.TEMPORARY_CONFLICT_TOPIC_TEMPLATES.get(topic)
        if variants:
            variant = event.get("topic_variant", 0)
            if not isinstance(variant, int) or not 0 <= variant < len(variants):
                variant = 0
            reason = variants[variant]
        opening = (
            f"{labels[0]}与{labels[1]}"
            if len(labels) > 1 and labels[1]
            else labels[0]
        )
        return f"{opening}{reason}。" if reason else f"{opening}发生了争执。"

    def resolve_current_temporary_conflict(self, choice: str) -> dict:
        """在事件出现的当前 Turn 立即结算，普通经营计划仍可随后提交。"""
        event = self.get_current_temporary_conflict_event()
        if event is None:
            return {"success": False, "message": "当前没有可处理的临时事件"}
        if choice not in {"verbal", "gift"}:
            return {"success": False, "message": "无效的临时事件处理方式"}
        if not isinstance(event.get(f"{choice}_result"), dict):
            self.state.today_conflict_event = {"status": "no_event"}
            return {"success": False, "message": "临时事件数据已失效，本场事件已安全结束"}
        cost = self.TEMPORARY_CONFLICT_GIFT_COST if choice == "gift" else 0
        if self.state.balance < cost:
            return {"success": False, "message": "余额不足，无法准备水果或小礼物"}
        if cost:
            self.state.balance -= cost
            self.state.today_expenses["conflict_care"] = (
                self.state.today_expenses.get("conflict_care", 0) + cost
            )
        result = {"events": [], "conflict_choice": choice}
        self._apply_temporary_conflict_event(result)
        return {
            "success": True,
            "message": result["events"][-1] if result["events"] else "临时事件已处理",
            "cost": cost,
            "decisions_left": self.state.decisions_left,
        }

    def _visible_guest_label(self, npc_id: int) -> str:
        npc = next((item for item in self.npc_pool if item.id == npc_id), None)
        if npc is not None and npc.visit_type == "day" and isinstance(npc.campsite_slot, int):
            return f"{npc.campsite_slot}号营位客人"
        tent = self._find_occupied_tent_for_npc(npc_id)
        if tent is not None:
            return f"{tent.id}号帐篷住客"
        return "营地客人"
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

    def submit_turn_plan(self, free_actions: Optional[list], actions: Optional[list], conflict_choice: Optional[str] = None) -> dict:
        free_actions = [] if free_actions is None else free_actions
        actions = [] if actions is None else actions

        self._drop_expired_turn_plan()

        if self.state.turn not in (2, 3, 4, 5):
            return {"success": False, "message": "planning unavailable"}
        if self.state.pending_turn_plan is not None:
            return {"success": False, "message": "turn plan already submitted"}
        if not isinstance(free_actions, list) or not isinstance(actions, list):
            return {"success": False, "message": "invalid turn plan"}
        if self.get_current_temporary_conflict_event() is not None:
            return {
                "success": False,
                "error_code": "temporary_conflict_pending",
                "message": "temporary event choice required",
            }
        if conflict_choice is not None:
            return {"success": False, "message": "temporary event must be resolved before planning"}
        if len(actions) > self.MAX_TURN_PLAN_DECISION_ACTIONS:
            return {"success": False, "message": "too many actions"}
        if len(actions) > self.state.decisions_left:
            return {"success": False, "message": "too many actions"}

        normalized_free_actions = []
        for action_data in free_actions:
            ok, normalized, message = self._validate_turn_plan_action(action_data, "free")
            if not ok:
                return {"success": False, "message": message}
            normalized_free_actions.append(normalized)

        normalized_actions = []
        seen_actions = set()
        seen_repair_tent_ids = set()
        for action_data in actions:
            ok, normalized, message = self._validate_turn_plan_action(action_data, "decision")
            if not ok:
                return {"success": False, "message": message}
            action_name = normalized["action"]
            if action_name in {"improve_service", "clean_campsite", "buy_food_package"}:
                if action_name in seen_actions:
                    return {"success": False, "message": "duplicate decision action"}
                seen_actions.add(action_name)
            elif action_name == "repair_tent":
                tent_id = normalized["tent_id"]
                if tent_id in seen_repair_tent_ids:
                    return {"success": False, "message": "duplicate repair target"}
                seen_repair_tent_ids.add(tent_id)
            normalized_actions.append(normalized)

        target_day, target_turn = self._current_turn_plan_target()
        self.state.pending_turn_plan = {
            "target_day": target_day,
            "target_turn": target_turn,
            "free_actions": normalized_free_actions,
            "actions": normalized_actions,
        }
        self.state.decisions_left -= len(normalized_actions)
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
        elif action_name == "clean_campsite":
            result = self.clean_campsite(consume_decision=False)
        elif action_name == "make_post":
            result = self.make_post()
        elif action_name == "campfire":
            result = self.hold_campfire(consume_decision=False)
        elif action_name == "stargazing":
            result = self.go_stargazing(consume_decision=False)
        else:
            result = self.improve_service(consume_decision=False)
        affected_ids = result.get("affected_npc_ids", [])
        replay_targets = self._capture_player_action_targets(action_data, affected_ids)
        return {
            "action": action_name,
            "success": bool(result.get("success")),
            "message": result.get("message", ""),
            "_replay_targets": replay_targets,
            "affected_npc_ids": result.get("affected_npc_ids", []),
        }

    def _npc_replay_target(self, npc) -> dict:
        location = getattr(npc, "location", None)
        if isinstance(location, str) and location.startswith("tent_"):
            try:
                return {"type": "tent", "id": int(location.split("_", 1)[1])}
            except ValueError:
                pass
        if location == "campsite":
            return {"type": "campsite", "id": getattr(npc, "campsite_slot", None)}
        return {"type": "facility", "id": location or "campground"}

    def _capture_player_action_targets(self, action_data: dict, affected_ids=None) -> list[dict]:
        action = action_data.get("action")
        if action in {"improve_service", "clean_campsite", "campfire", "stargazing"}:
            wanted = set(affected_ids or [])
            return [self._npc_replay_target(npc) for npc in self.npc_pool if not npc.has_left and npc.id in wanted]
        if action in {"clean_tents", "repair_tent"}:
            ids = action_data.get("tent_ids") if action == "clean_tents" else [action_data.get("tent_id")]
            return [{"type": "tent", "id": tid} for tid in (ids or []) if tid is not None]
        if action == "make_post":
            return [{"type": "service_station"}]
        if action == "buy_food_package":
            return [{"type": "facility", "id": "dining"}]
        return []

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
        self.state.today_expenses["food"] = self.state.today_expenses.get("food", 0) + price
        self.state.food_stock += portions
        return {
            "success": True,
            "message": f"已购买{name}（{portions}份），花费{price}金币",
            "package_key": package_key,
            "portions": portions,
            "price": price,
        }

    def buy_food_package(self, package_key: str) -> dict:
        if self.state.day_end_completed:
            return {"success": False, "message": "日终清单已完成，请开启下一天"}
        if self.state.turn != 6:
            return {"success": False, "message": "食材预购只能在日终管理阶段（Turn 6）进行"}
        if self.state.last_food_preorder_day == self.state.day:
            return {"success": False, "message": "今天已经完成过食材预购"}

        result = self._buy_food_package(package_key)
        if result.get("success"):
            self.state.last_food_preorder_day = self.state.day
        return result

    def _execute_pending_turn_plan(
        self, result: dict, *, defer_improve_service: bool = False
    ) -> list[dict]:
        plan = self.state.pending_turn_plan
        result["plan_execution"] = {"free_actions": [], "actions": []}
        if not plan:
            return []
        if (plan.get("target_day"), plan.get("target_turn")) != self._current_turn_plan_target():
            result["events"].append("stale turn plan discarded")
            self.state.pending_turn_plan = None
            return []

        for action_data in plan.get("free_actions", []):
            result["plan_execution"]["free_actions"].append(
                self._run_turn_plan_action(action_data)
            )
        deferred_improve_service_actions = []
        for action_data in plan.get("actions", []):
            if defer_improve_service and action_data.get("action") in {
                "improve_service", "clean_campsite", "campfire"
            }:
                deferred_improve_service_actions.append(action_data)
                continue
            action_result = self._run_turn_plan_action(action_data)
            result["plan_execution"]["actions"].append(action_result)
            if action_result.get("success"):
                self._record_turn_plan_action_event(action_data, action_result)
            if (
                action_data.get("action") == "buy_food_package"
                and action_result.get("success")
            ):
                self._retry_waiting_dining_after_restock(result)
        result["conflict_choice"] = plan.get("conflict_choice")
        self.state.pending_turn_plan = None
        return deferred_improve_service_actions

    def _execute_deferred_improve_service_actions(
        self, result: dict, actions: list[dict]
    ) -> None:
        for action_data in actions:
            action_result = self._run_turn_plan_action(action_data)
            result["plan_execution"]["actions"].append(action_result)
            if action_result.get("success"):
                self._record_turn_plan_action_event(action_data, action_result)

    def _record_turn_plan_action_event(self, action_data: dict, action_result: dict) -> None:
        """把玩家经营动作写入统一日志；无实际信息的动作不单独记录。"""
        action = action_data.get("action")
        targets = action_result.pop("_replay_targets", []) or action_result.pop("replay_targets", [])
        event_type = {"buy_food_package": "food_restock", "improve_service": "improve_service", "clean_campsite": "clean_campsite", "campfire": "campfire", "stargazing": "stargazing"}.get(action, action)
        package = self.FOOD_PACKAGES.get(action_data.get("package_key"))
        data = {"name": package["name"], "portions": package["portions"]} if package else {}
        if event_type in {"improve_service", "clean_campsite", "campfire", "stargazing"}:
            return
        self._record_business_event(self.state.day, self.state.turn, event_type, guest_ids=action_result.get("affected_npc_ids", []), data=data, kind="action", merge=False, actor="player", action=action, targets=targets)

    def _apply_opening_food_gift(self):
        package = self.FOOD_PACKAGES[self.OPENING_FOOD_GIFT_PACKAGE]
        self.state.food_stock = package["portions"]
        self._append_event_history(
            self.state.day,
            self.state.turn,
            self._build_opening_food_gift_event(),
            "world",
        )

    def _build_opening_food_gift_event(self) -> str:
        package = self.FOOD_PACKAGES[self.OPENING_FOOD_GIFT_PACKAGE]
        package_label = f'{package["name"]}（{package["portions"]}份）'
        return (
            "开业物资已送达：为了帮助你顺利营业，"
            f"特别赠送食材{package_label}。祝你经营顺利！"
        )

    # -------------------------------------------------------------------------
    # JSON 快照持久化（session_id 分区，runtime_snapshot 为唯一权威存档）
    # -------------------------------------------------------------------------

    def _ensure_snapshot_table(self):
        """创建或确认 runtime_snapshot 表存在。失败不抛异常，不影响服务启动"""
        try:
            if self.use_postgres:
                if psycopg2 is None:
                    raise RuntimeError("检测到 DATABASE_URL，但未安装 psycopg2-binary")
                conn = psycopg2.connect(self.database_url)
            else:
                conn = sqlite3.connect(self.db_path)
            try:
                if self.use_postgres:
                    cursor = conn.cursor()
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS runtime_snapshot (
                            session_id TEXT PRIMARY KEY,
                            snapshot_json TEXT NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """)
                    cursor.execute("""
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'runtime_snapshot' AND column_name = 'session_id'
                    """)
                    if cursor.fetchone() is None:
                        cursor.execute("DROP TABLE runtime_snapshot")
                        cursor.execute("""
                            CREATE TABLE runtime_snapshot (
                                session_id TEXT PRIMARY KEY,
                                snapshot_json TEXT NOT NULL,
                                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                            )
                        """)
                else:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS runtime_snapshot (
                            session_id TEXT PRIMARY KEY,
                            snapshot_json TEXT NOT NULL,
                            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                        )
                    """)
                    columns = {
                        row[1] for row in conn.execute("PRAGMA table_info(runtime_snapshot)")
                    }
                    if "session_id" not in columns:
                        conn.execute("DROP TABLE runtime_snapshot")
                        conn.execute("""
                            CREATE TABLE runtime_snapshot (
                                session_id TEXT PRIMARY KEY,
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
                "npc_id_counter": self._npc_id_counter,
            }
            data = json.dumps(payload, ensure_ascii=False)
            if self.use_postgres:
                conn = psycopg2.connect(self.database_url)
            else:
                conn = sqlite3.connect(self.db_path)
            try:
                if self.use_postgres:
                    conn.cursor().execute("""
                        INSERT INTO runtime_snapshot (session_id, snapshot_json, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT(session_id) DO UPDATE SET
                            snapshot_json = EXCLUDED.snapshot_json,
                            updated_at = EXCLUDED.updated_at
                    """, (self.session_id, data))
                else:
                    conn.execute("""
                        INSERT INTO runtime_snapshot (session_id, snapshot_json, updated_at)
                        VALUES (?, ?, datetime('now', 'localtime'))
                        ON CONFLICT(session_id) DO UPDATE SET
                            snapshot_json = excluded.snapshot_json,
                            updated_at = excluded.updated_at
                    """, (self.session_id, data))
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
            if not self.use_postgres and not os.path.exists(self.db_path):
                return "no_snapshot"
            if self.use_postgres:
                conn = psycopg2.connect(self.database_url)
            else:
                conn = sqlite3.connect(self.db_path)
            try:
                if self.use_postgres:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT snapshot_json FROM runtime_snapshot WHERE session_id = %s",
                        (self.session_id,),
                    )
                    row = cursor.fetchone()
                else:
                    row = conn.execute(
                        "SELECT snapshot_json FROM runtime_snapshot WHERE session_id = ?",
                        (self.session_id,),
                    ).fetchone()
            finally:
                conn.close()
            if not row:
                return "no_snapshot"

            payload = json.loads(row[0])
            if not isinstance(payload, dict):
                return "load_error"

            required = {"snapshot_version", "state", "tents", "facilities",
                        "npc_pool", "npc_id_counter"}
            if not required.issubset(payload.keys()):
                return "load_error"

            if int(payload["snapshot_version"]) != self.SNAPSHOT_VERSION:
                return "load_error"

            # 原子恢复：所有对象先在局部构造，全部成功后再一次性赋值给实例字段
            raw_state = payload["state"]
            legacy_campsite_star_state = not {
                "campsite_star", "historical_highest_rating",
            }.issubset(raw_state)
            legacy_debt_settlement_state = (
                "startup_debt_settlement_completed" not in raw_state
            )
            state_fields = {f for f in GameState.__dataclass_fields__}
            restored_state = GameState()
            for key, value in raw_state.items():
                if key in state_fields:
                    setattr(restored_state, key, value)
            try:
                restored_state.campsite_star = min(
                    5, max(1, int(restored_state.campsite_star))
                )
            except (TypeError, ValueError):
                restored_state.campsite_star = 1
            try:
                historical_highest_rating = restored_state.historical_highest_rating
                restored_state.historical_highest_rating = (
                    min(5.0, max(1.0, float(historical_highest_rating)))
                    if historical_highest_rating is not None else None
                )
            except (TypeError, ValueError):
                restored_state.historical_highest_rating = None
            restored_state.today_expenses = {
                category: int((restored_state.today_expenses or {}).get(category, 0) or 0)
                for category in (
                    "food", "greenery", "repair", "conflict_care", "growth",
                    "lodging_consumables", "hot_spring_operating",
                )
            }
            restored_state.today_income = {
                category: int((restored_state.today_income or {}).get(category, 0) or 0)
                for category in (
                    "accommodation", "campsite", "dining", "entertainment",
                    "hot_spring", "nature_observation", "tip",
                )
            }
            restored_state.nature_observation_station_built = bool(
                restored_state.nature_observation_station_built
            )
            restored_state.nature_observation_intro_seen = bool(
                restored_state.nature_observation_intro_seen
            )
            restored_state.discovered_insects = self._normalize_discovered_insects(
                restored_state.discovered_insects
            )
            restored_state.unlocked_achievement_ids = self._normalize_achievement_ids(
                restored_state.unlocked_achievement_ids
            )
            # 旧快照在 Day 26 或之后没有结算标记时，无法可靠补造当时的
            # 自动扣款事实；只将其视为已处理，保留余额、债务和既有成就。
            if legacy_debt_settlement_state:
                restored_state.startup_debt_settlement_completed = (
                    restored_state.day >= self.STARTUP_DEBT_SETTLEMENT_DAY
                    or bool(
                        set(restored_state.unlocked_achievement_ids)
                        & self.DEBT_RESULT_ACHIEVEMENT_IDS
                    )
                )
            else:
                restored_state.startup_debt_settlement_completed = bool(
                    restored_state.startup_debt_settlement_completed
                )
            # 旧字段保留为接口兼容名称，但其正式含义已是 Day 26 晨间结算日。
            restored_state.repayment_deadline_day = self.STARTUP_DEBT_SETTLEMENT_DAY
            restored_state.pending_achievement_ids = [
                achievement_id
                for achievement_id in self._normalize_achievement_ids(
                    restored_state.pending_achievement_ids
                )
                if achievement_id in restored_state.unlocked_achievement_ids
            ]
            raw_history = (
                restored_state.event_history
                if isinstance(restored_state.event_history, list) else []
            )
            normalized_history = []
            used_sequences = {
                int(item.get("sequence", 0) or 0)
                for item in raw_history
                if isinstance(item, dict) and int(item.get("sequence", 0) or 0) > 0
            }
            next_legacy_sequence = max(used_sequences, default=0)
            for item in raw_history:
                if not isinstance(item, dict):
                    continue
                normalized_item = dict(item)
                sequence = int(normalized_item.get("sequence", 0) or 0)
                if sequence <= 0:
                    next_legacy_sequence += 1
                    while next_legacy_sequence in used_sequences:
                        next_legacy_sequence += 1
                    sequence = next_legacy_sequence
                    normalized_item["sequence"] = sequence
                    used_sequences.add(sequence)
                normalized_item.setdefault("event_type", "legacy")
                if not isinstance(normalized_item.get("guest_ids"), list):
                    normalized_item["guest_ids"] = []
                if not isinstance(normalized_item.get("data"), dict):
                    normalized_item["data"] = {}
                normalized_history.append(normalized_item)
            restored_state.event_history = normalized_history
            pending_conflict = restored_state.today_conflict_event
            if (
                isinstance(pending_conflict, dict)
                and pending_conflict.get("status") == "scheduled"
                and ("mediate_result" in pending_conflict or "ignore_result" in pending_conflict)
                and not {"verbal_result", "gift_result"}.issubset(pending_conflict)
            ):
                restored_state.today_conflict_event = {"status": "no_event"}
            restored_state.event_sequence = max(
                int(restored_state.event_sequence or 0),
                max(
                    (int(item.get("sequence", 0) or 0)
                     for item in restored_state.event_history if isinstance(item, dict)),
                    default=0,
                ),
            )

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
                # v1 线上快照没有体验账本字段：它们不是历史事件，安全地从 0 开始。
                normalized_ndata.setdefault("positive_experience_total", 0.0)
                normalized_ndata.setdefault("negative_experience_total", 0.0)
                normalized_ndata.setdefault("growth_served_recorded", False)
                normalized_ndata.setdefault("growth_dining_recorded", False)
                normalized_ndata.setdefault("growth_paid_entertainment_recorded", False)
                restored_npc_pool.append(NPCGroup(**normalized_ndata))
            restored_npc_id_counter = int(payload["npc_id_counter"])

            # 全部构造成功才提交到实例字段
            self.state = restored_state
            self.tents = restored_tents
            self.facilities = restored_facilities
            self.npc_pool = restored_npc_pool
            self._npc_id_counter = restored_npc_id_counter
            if legacy_campsite_star_state:
                self._migrate_legacy_campsite_star_state(raw_state)
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
            name="绿化", level=0, greenery_satisfaction=2.0, greenery_decay_rate=0.5
        )

    # -------------------------------------------------------------------------
    # NPC ID 生成
    # -------------------------------------------------------------------------

    def _next_npc_id(self) -> int:
        """生成全局唯一NPC ID"""
        self._npc_id_counter += 1
        return self._npc_id_counter

    def _record_served_group_once(self, npc: NPCGroup) -> bool:
        """记录实际成功入场的客组；同一到访客组最多记录一次。"""
        if npc.growth_served_recorded:
            return False
        npc.growth_served_recorded = True
        self.state.total_served_groups += 1
        self._unlock_achievement("first_served_group")
        for threshold in (50, 100, 150):
            if self.state.total_served_groups >= threshold:
                self._unlock_achievement(f"served_groups_{threshold}")
        return True

    def _record_successful_dining_once(self, npc: NPCGroup) -> bool:
        """记录实际成功用餐的客组；正常与补货补救共用此入口。"""
        if npc.growth_dining_recorded:
            return False
        npc.growth_dining_recorded = True
        self.state.successful_dining_groups += 1
        return True

    def _record_successful_paid_entertainment_once(self, npc: NPCGroup) -> bool:
        """记录实际完成收费娱乐的客组；免费娱乐不调用此入口。"""
        if npc.growth_paid_entertainment_recorded:
            return False
        npc.growth_paid_entertainment_recorded = True
        self.state.successful_paid_entertainment_groups += 1
        return True

    def _get_valid_growth_facility_level(self, facility_name: str) -> int:
        """读取成长节点使用的设施等级；异常状态必须显式暴露。"""
        level = self.facilities[facility_name].level
        if type(level) is not int or level not in (0, 1, 2):
            raise ValueError(
                f"invalid growth facility level: {facility_name}={level!r}"
            )
        return level

    def get_growth_progress(self) -> dict:
        """只读返回成长进度；成长节点从当前解锁与设施等级实时派生。"""
        unlocked_tent_nodes = sum(
            1
            for tent_id in range(2, 7)
            if self.tents.get(tent_id) is not None
            and self.tents[tent_id].is_unlocked
        )
        dining_nodes = self._get_valid_growth_facility_level("dining")
        entertainment_nodes = self._get_valid_growth_facility_level("entertainment")
        greenery_nodes = self._get_valid_growth_facility_level("greenery")
        completed_growth_nodes = (
            unlocked_tent_nodes
            + dining_nodes
            + entertainment_nodes
            + greenery_nodes
        )
        return {
            "unlocked_tent_nodes": unlocked_tent_nodes,
            "dining_nodes": dining_nodes,
            "entertainment_nodes": entertainment_nodes,
            "greenery_nodes": greenery_nodes,
            "completed_growth_nodes": completed_growth_nodes,
            "total_served_groups": self.state.total_served_groups,
            "successful_dining_groups": self.state.successful_dining_groups,
            "successful_paid_entertainment_groups": (
                self.state.successful_paid_entertainment_groups
            ),
            "successful_greenery_maintenance_count": (
                self.state.successful_greenery_maintenance_count
            ),
            "hot_spring_built": self.state.hot_spring_built,
            "campsite_star": self.state.campsite_star,
            "historical_highest_rating": self.state.historical_highest_rating,
            "operating_day": self.state.day,
        }

    def _get_growth_project_operation_status(self, project: dict) -> tuple:
        """只读计算一个成长项目的经营条件与进度。"""
        operation = project["operation"]
        if operation == "day":
            required_day = project["required_day"]
            return (
                self.state.day >= required_day,
                "operating_day_required",
                {
                    "current_operating_day": self.state.day,
                    "required_operating_day": required_day,
                },
            )
        if operation == "campsite_star":
            required_star = project["required_campsite_star"]
            return (
                self.state.campsite_star >= required_star,
                "campsite_star_required",
                {
                    "current_campsite_star": self.state.campsite_star,
                    "required_campsite_star": required_star,
                },
            )
        if operation == "served_or_day":
            required_served = project["required_served_groups"]
            fallback_day = project["fallback_operating_day"]
            return (
                self.state.total_served_groups >= required_served
                or self.state.day >= fallback_day,
                "served_groups_or_days_required",
                {
                    "current_served_groups": self.state.total_served_groups,
                    "required_served_groups": required_served,
                    "current_operating_day": self.state.day,
                    "fallback_operating_day": fallback_day,
                },
            )
        if operation == "hot_spring_operation":
            required_served = project["required_served_groups"]
            fallback_day = project["fallback_operating_day"]
            return (
                self.state.total_served_groups >= required_served
                or self.state.day >= fallback_day,
                "served_groups_or_days_required",
                {
                    "current_served_groups": self.state.total_served_groups,
                    "required_served_groups": required_served,
                    "current_operating_day": self.state.day,
                    "fallback_operating_day": fallback_day,
                },
            )

        requirement_map = {
            "successful_dining": (
                "successful_dining_groups",
                "required_successful_dining_groups",
                "successful_dining_required",
            ),
            "successful_paid_entertainment": (
                "successful_paid_entertainment_groups",
                "required_successful_paid_entertainment_groups",
                "successful_paid_entertainment_required",
            ),
            "greenery_maintenance": (
                "successful_greenery_maintenance_count",
                "required_successful_greenery_maintenance_count",
                "greenery_maintenance_required",
            ),
        }
        state_field, project_field, unmet_code = requirement_map[operation]
        current_value = getattr(self.state, state_field)
        required_value = project[project_field]
        return (
            current_value >= required_value,
            unmet_code,
            {
                f"current_{state_field}": current_value,
                project_field: required_value,
            },
        )

    def get_growth_project_catalog(self) -> list:
        """只读返回成长项目及其当前购买资格。"""
        facility_levels = {
            name: self._get_valid_growth_facility_level(name)
            for name in ("dining", "entertainment", "greenery")
        }
        management_phase_open = (
            self.state.turn == 6 and not self.state.day_end_completed
        )
        catalog = []
        for project in self.GROWTH_PROJECT_CATALOG:
            category = project["category"]
            unmet_conditions = []
            if category == "tent":
                target_tent_id = project["target_tent_id"]
                completed = self.tents[target_tent_id].is_unlocked
                prerequisite_met = self.tents[
                    project["prerequisite_tent_id"]
                ].is_unlocked
                progress = {"target_tent_id": target_tent_id}
                prerequisite_unmet_code = "previous_tent_required"
            elif category == "hot_spring":
                completed = self.state.hot_spring_built
                prerequisite_met = True
                progress = {}
                prerequisite_unmet_code = ""
            elif category == "nature_observation_station":
                completed = self.state.nature_observation_station_built
                prerequisite_met = True
                progress = {}
                prerequisite_unmet_code = ""
            else:
                current_level = facility_levels[category]
                completed = current_level >= project["target_level"]
                prerequisite_met = completed or current_level == project["required_level"]
                progress = {
                    "current_level": current_level,
                    "required_level": project["required_level"],
                    "target_level": project["target_level"],
                }
                prerequisite_unmet_code = "previous_level_required"

            operation_requirement_met, operation_unmet_code, operation_progress = (
                self._get_growth_project_operation_status(project)
            )
            progress.update(operation_progress)
            affordable = self.state.balance >= project["price"]

            if completed:
                unmet_conditions.append("already_completed")
            else:
                if not prerequisite_met:
                    unmet_conditions.append(prerequisite_unmet_code)
                if not operation_requirement_met:
                    unmet_conditions.append(operation_unmet_code)
                if not affordable:
                    unmet_conditions.append("insufficient_balance")
                if self.state.turn != 6:
                    unmet_conditions.append("turn_6_required")

            catalog.append(
                {
                    "project_id": project["project_id"],
                    "category": category,
                    "display_name": project["display_name"],
                    "price": project["price"],
                    "completed": completed,
                    "prerequisite_met": prerequisite_met,
                    "operation_requirement_met": operation_requirement_met,
                    "affordable": affordable,
                    "management_phase_open": management_phase_open,
                    "can_purchase_now": (
                        not completed
                        and prerequisite_met
                        and operation_requirement_met
                        and affordable
                        and management_phase_open
                    ),
                    "unmet_conditions": unmet_conditions,
                    "progress": progress,
                }
            )
        return catalog

    def purchase_growth_project(self, project_id: str) -> dict:
        """原子购买成长项目。"""
        project_definition = next(
            (
                project
                for project in self.GROWTH_PROJECT_CATALOG
                if project["project_id"] == project_id
            ),
            None,
        )
        if project_definition is None:
            return {
                "success": False,
                "project_id": project_id,
                "error_code": "unknown_growth_project",
            }

        category = project_definition["category"]
        if category not in (
            "tent", "dining", "entertainment", "greenery", "hot_spring",
            "nature_observation_station",
        ):
            return {
                "success": False,
                "project_id": project_id,
                "category": category,
                "error_code": "growth_project_category_not_implemented",
            }

        project_status = next(
            project
            for project in self.get_growth_project_catalog()
            if project["project_id"] == project_id
        )
        if not project_status["can_purchase_now"]:
            return {
                "success": False,
                "project_id": project_id,
                "category": category,
                "error_code": "growth_project_not_purchasable",
                "unmet_conditions": project_status["unmet_conditions"],
            }

        balance_before = self.state.balance
        if category == "tent":
            target_tent_id = project_definition["target_tent_id"]
            tent = self.tents[target_tent_id]
            previous_unlocked = tent.is_unlocked
            previous_next_breakdown_turn = tent.next_breakdown_turn
            try:
                self.state.balance -= project_status["price"]
                tent.is_unlocked = True
                self._set_next_breakdown(tent)
            except Exception as exc:
                self.state.balance = balance_before
                tent.is_unlocked = previous_unlocked
                tent.next_breakdown_turn = previous_next_breakdown_turn
                return {
                    "success": False,
                    "project_id": project_id,
                    "category": category,
                    "error_code": "growth_project_purchase_failed",
                    "error": str(exc),
                }

            self.state.today_expenses["growth"] = self.state.today_expenses.get("growth", 0) + project_status["price"]
            self._record_growth_project_achievements(project_id)
            return {
                "success": True,
                "project_id": project_id,
                "category": category,
                "display_name": project_status["display_name"],
                "price": project_status["price"],
                "balance_before": balance_before,
                "balance_after": self.state.balance,
                "target_tent_id": target_tent_id,
                "completed_growth_nodes": self.get_growth_progress()[
                    "completed_growth_nodes"
                ],
            }

        if category == "hot_spring":
            previous_built = self.state.hot_spring_built
            try:
                self.state.balance -= project_status["price"]
                self.state.hot_spring_built = True
            except Exception as exc:
                self.state.balance = balance_before
                self.state.hot_spring_built = previous_built
                return {
                    "success": False,
                    "project_id": project_id,
                    "category": category,
                    "error_code": "growth_project_purchase_failed",
                    "error": str(exc),
                }
            self.state.today_expenses["growth"] = self.state.today_expenses.get("growth", 0) + project_status["price"]
            self._record_growth_project_achievements(project_id)
            return {
                "success": True,
                "project_id": project_id,
                "category": category,
                "display_name": project_status["display_name"],
                "price": project_status["price"],
                "balance_before": balance_before,
                "balance_after": self.state.balance,
                "hot_spring_built": self.state.hot_spring_built,
                "completed_growth_nodes": self.get_growth_progress()[
                    "completed_growth_nodes"
                ],
            }

        if category == "nature_observation_station":
            previous_built = self.state.nature_observation_station_built
            try:
                self.state.balance -= project_status["price"]
                self.state.nature_observation_station_built = True
            except Exception as exc:
                self.state.balance = balance_before
                self.state.nature_observation_station_built = previous_built
                return {
                    "success": False,
                    "project_id": project_id,
                    "category": category,
                    "error_code": "growth_project_purchase_failed",
                    "error": str(exc),
                }
            self.state.today_expenses["growth"] = (
                self.state.today_expenses.get("growth", 0) + project_status["price"]
            )
            return {
                "success": True,
                "project_id": project_id,
                "category": category,
                "display_name": project_status["display_name"],
                "price": project_status["price"],
                "balance_before": balance_before,
                "balance_after": self.state.balance,
                "nature_observation_station_built": (
                    self.state.nature_observation_station_built
                ),
                "message": "自然观察站建成了，从明天开始客人将有机会参加自然观察活动。",
                "completed_growth_nodes": self.get_growth_progress()[
                    "completed_growth_nodes"
                ],
            }

        if category == "greenery":
            greenery = self.facilities["greenery"]
            previous_level = greenery.level
            previous_satisfaction = greenery.greenery_satisfaction
            previous_processed_today = self.state.greenery_processed_today
            try:
                self.state.balance -= project_status["price"]
                greenery.level = project_definition["target_level"]
                greenery.greenery_satisfaction = round(
                    min(
                        self.GREENERY_LEVEL_MAX[greenery.level],
                        greenery.greenery_satisfaction + 2.0,
                    ),
                    1,
                )
                self.state.greenery_processed_today = True
            except Exception as exc:
                self.state.balance = balance_before
                greenery.level = previous_level
                greenery.greenery_satisfaction = previous_satisfaction
                self.state.greenery_processed_today = previous_processed_today
                return {
                    "success": False,
                    "project_id": project_id,
                    "category": category,
                    "error_code": "growth_project_purchase_failed",
                    "error": str(exc),
                }

            self.state.today_expenses["growth"] = self.state.today_expenses.get("growth", 0) + project_status["price"]
            self._record_growth_project_achievements(project_id)
            return {
                "success": True,
                "project_id": project_id,
                "category": category,
                "display_name": project_status["display_name"],
                "price": project_status["price"],
                "balance_before": balance_before,
                "balance_after": self.state.balance,
                "previous_level": previous_level,
                "target_level": project_definition["target_level"],
                "completed_growth_nodes": self.get_growth_progress()[
                    "completed_growth_nodes"
                ],
            }

        facility = self.facilities[category]
        previous_level = facility.level
        try:
            self.state.balance -= project_status["price"]
            facility.level = project_definition["target_level"]
        except Exception as exc:
            self.state.balance = balance_before
            facility.level = previous_level
            return {
                "success": False,
                "project_id": project_id,
                "category": category,
                "error_code": "growth_project_purchase_failed",
                "error": str(exc),
            }

        self.state.today_expenses["growth"] = self.state.today_expenses.get("growth", 0) + project_status["price"]
        self._record_growth_project_achievements(project_id)
        result = {
            "success": True,
            "project_id": project_id,
            "category": category,
            "display_name": project_status["display_name"],
            "price": project_status["price"],
            "balance_before": balance_before,
            "balance_after": self.state.balance,
            "previous_level": previous_level,
            "target_level": project_definition["target_level"],
            "completed_growth_nodes": self.get_growth_progress()[
                "completed_growth_nodes"
            ],
        }
        if category == "entertainment":
            result["message"] = {
                1: "娱乐升级成功，新增：投壶、射箭体验。",
                2: "娱乐升级成功，新增：露天电影、便携 K 歌设备租赁。",
            }.get(project_definition["target_level"], "娱乐升级成功。")
        return result

    # -------------------------------------------------------------------------
    # 修复 #3 辅助方法：判断帐篷是否为今日预定帐篷
    # -------------------------------------------------------------------------

    def _is_today_reserved_tent(self, tent_id: int) -> bool:
        """判断帐篷是否为今日预定帐篷"""
        for entry in self.state.today_arrival_plan:
            if entry.get("planned_day") != self.state.day:
                continue
            if entry.get("source") != "reservation":
                continue
            if entry.get("arrival_status") != "pending":
                continue
            if entry.get("tent_id") == tent_id:
                return True
        return False

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

    def _format_guest_labels(self, guest_ids: list[int]) -> str:
        """把内部客组 ID 统一转成紧凑的玩家可见位置称呼。"""
        campsite_slots = []
        tent_ids = []
        for npc_id in guest_ids:
            npc = self._find_npc(npc_id)
            if npc is not None and npc.visit_type == "day" and isinstance(npc.campsite_slot, int):
                campsite_slots.append(npc.campsite_slot)
                continue
            tent = self._find_occupied_tent_for_npc(npc_id)
            if tent is not None:
                tent_ids.append(tent.id)
        parts = []
        if campsite_slots:
            parts.append("、".join(str(slot) for slot in sorted(set(campsite_slots))) + "号营位客人")
        if tent_ids:
            parts.append("、".join(str(tent_id) for tent_id in sorted(set(tent_ids))) + "号帐篷住客")
        return "及".join(parts)

    def _format_business_event(self, event_type: str, guest_ids: list[int], data: dict) -> str:
        guests = self._format_guest_labels(guest_ids)
        if event_type == "temporary_conflict":
            choice = (
                "你进行了口头调解"
                if data.get("choice") == "verbal"
                else "你准备了水果或小礼物安抚"
            )
            affected = self._format_guest_labels(data.get("affected_guest_ids", []))
            if not affected:
                outcome = "双方很快平静下来。"
            elif len(data.get("affected_guest_ids", [])) == 1:
                outcome = f"但{affected}仍有些不满。"
            else:
                outcome = "但双方情绪都没有完全平复。"
            opening = self._format_temporary_conflict_opening(
                [guests, ""], data
            ).rstrip("。")
            return f"临时事件：{opening}。{choice}，{outcome}"
        if event_type == "arrival_day":
            return f"{guests}到达营地。"
        if event_type == "arrival_overnight":
            return f"{guests}入住营地。"
        if event_type == "arrival":
            campsite_slots = []
            tent_ids = []
            for npc_id in guest_ids:
                npc = self._find_npc(npc_id)
                if npc is not None and npc.visit_type == "day" and isinstance(npc.campsite_slot, int):
                    campsite_slots.append(npc.campsite_slot)
                else:
                    tent = self._find_occupied_tent_for_npc(npc_id)
                    if tent is not None:
                        tent_ids.append(tent.id)
            parts = []
            if campsite_slots:
                parts.append(f"{'、'.join(str(slot) for slot in sorted(set(campsite_slots)))}号营位客人到达营地")
            if tent_ids:
                parts.append(f"{'、'.join(str(tent_id) for tent_id in sorted(set(tent_ids)))}号帐篷住客入住营地")
            return "；".join(parts) + "。"
        if event_type == "dining_completed":
            if "income" in data:
                return f"{guests}完成用餐，收入{data['income']}金币。"
            return f"{guests}完成用餐。"
        if event_type == "dining_shortage":
            return f"{guests}想要用餐，但因食材不足未能提供。"
        if event_type == "entertainment_completed":
            items = data.get("items", [])
            grouped_items = {}
            for item in items:
                key = (
                    tuple(item.get("activities", [])),
                    item.get("tier_name"),
                    item.get("income", 0),
                    item.get("satisfaction_gain", 0),
                )
                grouped_items.setdefault(key, []).append(item.get("npc_id"))
            parts = []
            for (activities, tier_name, income, satisfaction), guest_ids in grouped_items.items():
                label = self._format_guest_labels(guest_ids)
                if not label or not activities:
                    continue
                if "收费娱乐" in activities and "免费娱乐" in activities:
                    action_text = "参加收费娱乐和免费娱乐"
                    result = f"整组收费+{income}，整组满意度+{satisfaction}"
                elif "收费娱乐" in activities:
                    action_text = f"参加{tier_name}" if tier_name else "参加收费娱乐"
                    result = f"整组收费+{income}，整组满意度+{satisfaction}"
                else:
                    action_text = "参加免费娱乐"
                    result = f"整组满意度+{satisfaction}"
                parts.append(f"{label}{action_text}，{result}")
            return "；".join(parts) + "。" if parts else f"{guests}参与娱乐。"
        if event_type == "hot_spring_completed":
            if "income" in data:
                satisfaction = data.get("satisfaction_gain")
                suffix = f"，满意度+{satisfaction}" if satisfaction is not None else ""
                return f"{guests}使用温泉，收入{data['income']}金币{suffix}。"
            return f"{guests}使用温泉。"
        if event_type == "nature_observation":
            if data.get("result") == "not_found":
                return f"{guests}参加自然观察，收入{data.get('income', 0)}金币，没有新的发现。"
            insect_name = data.get("insect_name") or data.get("insect_id", "未知昆虫")
            discovery_text = "首次点亮图鉴" if data.get("is_new_discovery") else "再次观察到"
            return f"{guests}参加自然观察，收入{data.get('income', 0)}金币，{discovery_text}{insect_name}。"
        if event_type == "review_pending":
            return f"有{data.get('count', 0)}组客人留下评价，将于次日晨间结算。"
        if event_type == "repay_debt":
            return f"偿还启动负债{data['amount']}金币。"
        if event_type == "improve_service":
            return f"服务提升，{guests}满意度+5。"
        if event_type == "clean_campsite":
            return f"清洁营地，{guests}满意度+2。"
        if event_type == "campfire":
            return f"篝火进行，{guests}享受了篝火。"
        if event_type == "stargazing":
            return f"星空体验，{guests}感受了星空。"
        if event_type == "food_restock":
            return f"已购买{data['name']}，补充{data['portions']}份食材。"
        if event_type == "tips":
            return f"今日收到小费 {data['amount']} 金币。"
        if event_type == "day_departure":
            return f"{data['count']}组日间客离场。"
        if event_type == "food_discard":
            return f"今日营业结束，剩余{data['portions']}份食材已作废。"
        return str(data.get("text", "")).strip()

    def _record_business_event(
        self, day: int, turn: int, event_type: str, *, guest_ids: Optional[list[int]] = None,
        data: Optional[dict] = None, kind: str = "world", merge: bool = True,
        actor: Optional[str] = None, action: Optional[str] = None,
        targets: Optional[list[dict]] = None,
    ) -> None:
        """统一记录、聚合、格式化经营日志；只接受结构化事实，不接受调用方拼文案。"""
        guest_ids = list(guest_ids or [])
        data = dict(data or {})
        text = self._format_business_event(event_type, guest_ids, data)
        if not text:
            return
        if merge and self.state.event_history:
            previous = self.state.event_history[-1]
            if (
                previous.get("day") == day and previous.get("turn") == turn
                and previous.get("event_type") == event_type
            ):
                merged_guests = list(dict.fromkeys(previous.get("guest_ids", []) + guest_ids))
                previous["guest_ids"] = merged_guests
                if event_type == "day_departure":
                    previous["data"]["count"] = previous.get("data", {}).get("count", 0) + data.get("count", 0)
                elif event_type == "review_pending":
                    previous["data"]["count"] = previous.get("data", {}).get("count", 0) + data.get("count", 0)
                elif event_type in {"dining_completed", "hot_spring_completed"}:
                    for field in ("income", "food_portions", "satisfaction_gain"):
                        previous["data"][field] = previous.get("data", {}).get(field, 0) + data.get(field, 0)
                previous["text"] = self._format_business_event(event_type, merged_guests, previous.get("data", data))
                return
        self._append_event_history(day, turn, text, kind, event_type, guest_ids, data)
        if actor is not None:
            self.state.event_history[-1].update({"actor": actor, "action": action, "targets": list(targets or [])})

    def _append_event_history(
        self, day: int, turn: int, text: str, kind: str,
        event_type: str = "legacy", guest_ids: Optional[list[int]] = None,
        data: Optional[dict] = None,
    ) -> None:
        """统一日志底层写入：固定真实 day/turn，并记录同 Turn 内发生顺序。"""
        if not isinstance(text, str) or not text.strip():
            return
        self.state.event_sequence += 1
        entry = {
            "day": int(day),
            "turn": int(turn),
            "text": text.strip(),
            "kind": kind,
            "sequence": self.state.event_sequence,
            "event_type": event_type,
            "guest_ids": list(guest_ids or []),
            "data": dict(data or {}),
        }
        self.state.event_history.append(entry)
        if len(self.state.event_history) > self.EVENT_HISTORY_LIMIT:
            del self.state.event_history[:-self.EVENT_HISTORY_LIMIT]

    def _append_result_events_to_history(self, day: int, turn: int, events: list) -> None:
        """过滤流程提示后，统一记录公共结果中的正式世界事件。"""
        ignored = {
            "stale turn plan discarded",
            "请先提交日终经营清单（day_end_actions）",
            "日终清单已完成，请调用 start_next_day 开启下一天",
            "=== 日终管理阶段 ===",
        }
        for text in events:
            if text not in ignored:
                self._append_event_history(day, turn, text, "world")

    def _snapshot_turn_business_state(self) -> dict:
        """保存营业回合摘要所需的轻量执行前状态。"""
        action_statuses = {}
        arrival_statuses = {}
        for entry in self.state.today_arrival_plan:
            npc_id = entry.get("npc_id")
            arrival_statuses[npc_id] = entry.get("arrival_status")
            for index, action in enumerate(entry.get("planned_actions", [])):
                action_statuses[(npc_id, index)] = action.get("status")

        return {
            "income": dict(self.state.today_income),
            "tents": {
                tent_id: {"status": tent.status, "occupied_by": tent.occupied_by}
                for tent_id, tent in self.tents.items()
            },
            "npcs": {
                npc.id: {
                    "visit_type": npc.visit_type,
                    "has_left": npc.has_left,
                    "review_left": npc.review_left,
                    "campsite_slot": npc.campsite_slot,
                }
                for npc in self.npc_pool
            },
            "arrival_statuses": arrival_statuses,
            "action_statuses": action_statuses,
            "pending_review_ids": {
                review.get("npc_id")
                for review in self.state.pending_reviews
                if isinstance(review, dict)
            },
        }

    @staticmethod
    def _tent_id_from_location(location: str) -> Optional[int]:
        if not isinstance(location, str) or not location.startswith("tent_"):
            return None
        try:
            return int(location.split("_", 1)[1])
        except ValueError:
            return None

    def _append_turn_business_summaries(
        self, snapshot: dict, day: int, turn: int
    ) -> None:
        """根据营业回合前后正式状态写入按同类事件汇总的日志。"""
        income_before = snapshot["income"]
        income_delta = {
            key: self.state.today_income.get(key, 0) - income_before.get(key, 0)
            for key in self.state.today_income
        }
        current_npcs = {npc.id: npc for npc in self.npc_pool}
        current_entries = {
            entry.get("npc_id"): entry
            for entry in self.state.today_arrival_plan
        }

        def has_structured_event(event_type: str) -> bool:
            return any(
                event.get("day") == day and event.get("turn") == turn
                and event.get("event_type") == event_type
                for event in self.state.event_history
            )

        newly_arrived_entries = [
            entry
            for npc_id, entry in current_entries.items()
            if (
                snapshot["arrival_statuses"].get(npc_id) != "arrived"
                and entry.get("arrival_status") == "arrived"
            )
        ]

        campsite_slots = sorted(
            npc.campsite_slot
            for entry in newly_arrived_entries
            if entry.get("visit_type") == "day"
            for npc in [current_npcs.get(entry.get("npc_id"))]
            if npc is not None
            and isinstance(npc.campsite_slot, int)
            and 1 <= npc.campsite_slot <= self.DAY_CAMPSITE_CAPACITY
        )
        day_reservation_slots = [
            npc.campsite_slot
            for entry in newly_arrived_entries
            if entry.get("visit_type") == "day"
            and entry.get("source") == "reservation"
            for npc in [current_npcs.get(entry.get("npc_id"))]
            if npc is not None and isinstance(npc.campsite_slot, int)
        ]
        day_natural_slots = [
            npc.campsite_slot
            for entry in newly_arrived_entries
            if entry.get("visit_type") == "day"
            and entry.get("source") != "reservation"
            for npc in [current_npcs.get(entry.get("npc_id"))]
            if npc is not None and isinstance(npc.campsite_slot, int)
        ]
        day_reservation_slots.sort()
        day_natural_slots.sort()
        if day_reservation_slots:
            slots = "、".join(f"{slot}号" for slot in day_reservation_slots)
            message = (
                f"1组日间预约客按约到达，进入{slots}营位。"
                if len(day_reservation_slots) == 1
                else f"{len(day_reservation_slots)}组日间预约客按约到达，分别进入{slots}营位。"
            )
            self._append_event_history(day, turn, message, "world", "arrival")
        if day_natural_slots:
            slots = "、".join(f"{slot}号" for slot in day_natural_slots)
            income = income_delta.get("campsite", 0)
            message = (
                f"1组日间游客到达{slots}营位，收入{income}金币。"
                if len(day_natural_slots) == 1
                else f"{len(day_natural_slots)}组日间游客到达{slots}营位，共收入{income}金币。"
            )
            self._append_event_history(day, turn, message, "world", "arrival")
        converted_npcs = [
            npc
            for npc_id, before in snapshot["npcs"].items()
            if before["visit_type"] == "day"
            and not before["has_left"]
            for npc in [current_npcs.get(npc_id)]
            if npc is not None
            and npc.visit_type == "overnight"
            and self._tent_id_from_location(npc.location) is not None
        ]
        converted_tent_ids = sorted(
            self._tent_id_from_location(npc.location) for npc in converted_npcs
        )

        overnight_arrival_entries = [
            entry
            for entry in newly_arrived_entries
            if entry.get("visit_type") == "overnight"
        ]
        overnight_tent_ids = sorted(
            tent_id
            for entry in overnight_arrival_entries
            for tent_id in [
                self._tent_id_from_location(
                    getattr(current_npcs.get(entry.get("npc_id")), "location", "")
                )
            ]
            if tent_id is not None
        )

        accommodation_delta = income_delta.get("accommodation", 0)
        converted_income = sum(
            self.TENT_PRICES[tent_id] for tent_id in converted_tent_ids
        )
        overnight_income = sum(
            self.TENT_PRICES[tent_id]
            for entry in overnight_arrival_entries
            if not entry.get("paid", False)
            for tent_id in [
                self._tent_id_from_location(
                    getattr(current_npcs.get(entry.get("npc_id")), "location", "")
                )
            ]
            if tent_id is not None
        )
        overnight_reservation_tent_ids = sorted(
            getattr(self._find_occupied_tent_for_npc(entry.get("npc_id")), "id", None)
            for entry in overnight_arrival_entries
            if entry.get("source") == "reservation"
            and getattr(self._find_occupied_tent_for_npc(entry.get("npc_id")), "id", None) is not None
        )
        overnight_natural_tent_ids = sorted(
            getattr(self._find_occupied_tent_for_npc(entry.get("npc_id")), "id", None)
            for entry in overnight_arrival_entries
            if entry.get("source") != "reservation"
            and getattr(self._find_occupied_tent_for_npc(entry.get("npc_id")), "id", None) is not None
        )
        if overnight_reservation_tent_ids:
            tents = "、".join(f"{tent_id}号" for tent_id in overnight_reservation_tent_ids)
            message = (
                f"1组过夜预约客按约到达，入住{tents}帐篷。"
                if len(overnight_reservation_tent_ids) == 1
                else f"{len(overnight_reservation_tent_ids)}组过夜预约客按约到达，分别入住{tents}帐篷。"
            )
            self._append_event_history(day, turn, message, "world", "arrival")
        if overnight_natural_tent_ids:
            tents = "、".join(f"{tent_id}号" for tent_id in overnight_natural_tent_ids)
            income = accommodation_delta
            message = (
                f"1组过夜客入住{tents}帐篷，收入{income}金币。"
                if len(overnight_natural_tent_ids) == 1
                else f"{len(overnight_natural_tent_ids)}组过夜客入住{tents}帐篷，共收入{income}金币。"
            )
            self._append_event_history(day, turn, message, "world", "arrival")
        if converted_tent_ids:
            count = len(converted_tent_ids)
            tent_text = "、".join(f"{tent_id}号" for tent_id in converted_tent_ids)
            income = (
                accommodation_delta
                if not overnight_tent_ids
                else converted_income
            )
            prefix = "共" if count > 1 else ""
            self._append_event_history(
                day,
                turn,
                f"{count}组客人选择留宿，入住{tent_text}帐篷，{prefix}新增住宿收入{income}金币。",
                "world",
                "day_to_overnight",
            )

        completed_actions = []
        for entry in self.state.today_arrival_plan:
            npc_id = entry.get("npc_id")
            for index, action in enumerate(entry.get("planned_actions", [])):
                if (
                    snapshot["action_statuses"].get((npc_id, index)) != "completed"
                    and action.get("status") == "completed"
                ):
                    completed_actions.append({
                        "action": action.get("action"),
                        "npc_id": npc_id,
                        "data": dict(action),
                    })

        summary_definitions = (
            ("dining", "dining", "完成用餐"),
            ("hot_spring", "hot_spring", "使用温泉"),
        )
        food_shortage_dining_count = sum(
            1
            for entry in self.state.today_arrival_plan
            for index, action in enumerate(entry.get("planned_actions", []))
            if action.get("action") == "dining"
            and action.get("status") == "waiting_for_restock"
            and snapshot["action_statuses"].get((entry.get("npc_id"), index))
            != "waiting_for_restock"
        )
        if food_shortage_dining_count and not has_structured_event("dining_shortage"):
            self._append_event_history(
                day,
                turn,
                f"{food_shortage_dining_count}组客人想要用餐，但因食材不足未能提供。",
                "world",
                "dining_shortage",
            )

        def visible_guest_label(npc_id):
            npc = current_npcs.get(npc_id)
            before = snapshot["npcs"].get(npc_id, {})
            visit_type = npc.visit_type if npc is not None else before.get("visit_type")
            if visit_type == "day":
                campsite_slot = (
                    getattr(npc, "campsite_slot", None)
                    if npc is not None
                    else None
                )
                if not isinstance(campsite_slot, int):
                    campsite_slot = before.get("campsite_slot")
                if isinstance(campsite_slot, int) and 1 <= campsite_slot <= self.DAY_CAMPSITE_CAPACITY:
                    return f"{campsite_slot}号营位客人"
                return None
            if visit_type == "overnight":
                tent = self._find_occupied_tent_for_npc(npc_id)
                if tent is not None:
                    return f"{tent.id}号帐篷住客"
            return None

        for action_name, income_key, display_label in summary_definitions:
            action_items = [
                item for item in completed_actions
                if item["action"] == action_name
            ]
            guest_labels = [
                visible_guest_label(item["npc_id"])
                for item in action_items
            ]
            guest_labels = [label for label in guest_labels if label]
            if guest_labels and not has_structured_event(
                f"{action_name}_completed"
            ):
                income = income_delta.get(income_key, 0)
                guests_text = "、".join(guest_labels)
                income_text = "收入" if len(guest_labels) == 1 else "共收入"
                self._append_event_history(
                    day, turn,
                    f"{guests_text}{display_label}，{income_text}{income}金币。",
                    "world",
                    f"{action_name}_completed",
                )

        entertainment_by_guest = {}
        for item in completed_actions:
            if item["action"] not in ("paid_entertainment", "free_entertainment"):
                continue
            entertainment_by_guest.setdefault(item["npc_id"], set()).add(item["action"])
        if entertainment_by_guest:
            entertainment_items = []
            for npc_id, action_names in entertainment_by_guest.items():
                action_items = [
                    item for item in completed_actions
                    if item["npc_id"] == npc_id
                    and item["action"] in action_names
                ]
                paid_item = next(
                    (item for item in action_items if item["action"] == "paid_entertainment"),
                    None,
                )
                free_item = next(
                    (item for item in action_items if item["action"] == "free_entertainment"),
                    None,
                )
                activities = []
                if paid_item is not None:
                    activities.append("收费娱乐")
                if free_item is not None:
                    activities.append("免费娱乐")
                paid_data = paid_item["data"] if paid_item else {}
                free_data = free_item["data"] if free_item else {}
                entertainment_items.append({
                    "npc_id": npc_id,
                    "activities": activities,
                    "tier_name": (
                        self.ENTERTAINMENT_TIER_OPTIONS.get(
                            paid_data.get("tier_key"), {}
                        ).get("display_name")
                    ),
                    "income": paid_data.get("charged_amount", 0),
                    "satisfaction_gain": (
                        paid_data.get("satisfaction_gain", 0)
                        + free_data.get("satisfaction_gain", 0)
                    ),
                })
            if entertainment_items:
                self._record_business_event(
                    day, turn, "entertainment_completed",
                    guest_ids=[item["npc_id"] for item in entertainment_items],
                    data={"items": entertainment_items}, merge=False,
                )

        departed_ids = [
            npc_id
            for npc_id, before in snapshot["npcs"].items()
            if not before["has_left"]
            and (
                npc_id not in current_npcs
                or current_npcs[npc_id].has_left
            )
        ]
        if departed_ids and not has_structured_event("day_departure"):
            text = f"{len(departed_ids)}组客人离场"
            self._append_event_history(day, turn, text + "。", "world", "day_departure")

        checkout_tent_ids = [
            tent_id for tent_id, before in snapshot["tents"].items()
            if before["occupied_by"] is not None
            and self.tents[tent_id].occupied_by is None
            and self.tents[tent_id].needs_cleaning
        ]
        if checkout_tent_ids:
            tents = "、".join(f"{tent_id}号" for tent_id in checkout_tent_ids)
            self._append_event_history(day, turn, f"{tents}帐篷住客退房。", "world", "checkout")

        broken_tent_ids = [
            tent_id for tent_id, before in snapshot["tents"].items()
            if before["status"] != "broken" and self.tents[tent_id].status == "broken"
        ]
        if broken_tent_ids:
            tents = "、".join(f"{tent_id}号" for tent_id in broken_tent_ids)
            self._append_event_history(day, turn, f"⚠️ {tents}帐篷出现故障，需要维修。", "world", "tent_breakdown")

        new_review_count = len({
            review.get("npc_id") for review in self.state.pending_reviews
            if isinstance(review, dict)
        } - snapshot["pending_review_ids"])
        if new_review_count:
            self._record_business_event(
                day, turn, "review_pending", data={"count": new_review_count}
            )

    def _format_guest_moment_label(self, npc_id: int, snapshot: dict) -> str:
        """为表现层镜头保留本 Turn 已离场客组的可见位置称呼。"""
        npc = self._find_npc(npc_id)
        before = snapshot.get("npcs", {}).get(npc_id, {})
        campsite_slot = getattr(npc, "campsite_slot", None)
        if not isinstance(campsite_slot, int):
            campsite_slot = before.get("campsite_slot")
        visit_type = getattr(npc, "visit_type", before.get("visit_type"))
        if visit_type == "day" and isinstance(campsite_slot, int):
            return f"{campsite_slot}号营位的客人"
        tent = self._find_occupied_tent_for_npc(npc_id)
        if tent is not None:
            return f"{tent.id}号帐篷住客"
        for tent_id, tent_before in snapshot.get("tents", {}).items():
            if tent_before.get("occupied_by") == npc_id:
                return f"{tent_id}号帐篷住客"
        return "这组客人"

    def _build_guest_moment_candidates(
        self, snapshot: dict, day: int, turn: int
    ) -> list[dict]:
        """只从本 Turn 已确定的经营事实构造表现层候选，不改写任何经营状态。"""
        candidates = []
        current_entries = {
            entry.get("npc_id"): entry for entry in self.state.today_arrival_plan
            if entry.get("planned_day") == day
        }

        completed_actions = []
        shortage_npc_ids = []
        for npc_id, entry in current_entries.items():
            for index, action in enumerate(entry.get("planned_actions", [])):
                before_status = snapshot.get("action_statuses", {}).get((npc_id, index))
                if before_status == action.get("status"):
                    continue
                if action.get("status") == "completed":
                    completed_actions.append((npc_id, action))
                elif (
                    action.get("action") == "dining"
                    and action.get("status") == "waiting_for_restock"
                ):
                    shortage_npc_ids.append(npc_id)

        for npc_id, action in completed_actions:
            if action.get("action") != "dining":
                continue
            menu = self.DINING_SET_MENUS.get(action.get("menu_key"))
            if menu is None:
                continue
            label = self._format_guest_moment_label(npc_id, snapshot)
            menu_name = menu["display_name"]
            candidates.append({
                "priority": "normal",
                "source": "dining",
                "npc_ids": [npc_id],
                "texts": (
                    f"{label}在菜单前看了一会儿，最后点了{menu_name}。",
                    f"到了饭点，{label}很快进了篝火厨房，选了{menu_name}。",
                    f"{label}围着餐台转了半圈，要了一份{menu_name}。",
                    f"{label}看了看今天的套餐，最后点了{menu_name}。",
                    f"开饭时间，{label}没怎么犹豫，选了{menu_name}。",
                    f"{label}和同伴商量了几句，最后点了{menu_name}。",
                    f"{label}把{menu_name}端回营位，坐下慢慢吃了起来。",
                ),
            })

        shortage_reactions = {
            0: (
                "听完说明后点了点头，说可以等一会儿。",
                "听完说明后表示理解，没多说什么。",
                "听完说明后把菜单放了回去，打算晚些再来。",
            ),
            1: (
                "听完说明后在餐饮区旁停了一会儿。",
                "听完说明后有些犹豫，又看了看餐台。",
                "听完说明后站在原地想了想。",
            ),
            2: (
                "听完说明后神情里明显带着些介意。",
                "听完说明后皱了皱眉，最后还是走开了。",
                "听完说明后有些不满地嘀咕了两句。",
            ),
        }
        shortage_templates = (
            "{label}原本准备吃饭，{reaction}",
            "{label}本想点一份餐，{reaction}",
            "{label}走到餐台前才得知暂时没有食材，{reaction}",
        )
        for npc_id in dict.fromkeys(shortage_npc_ids):
            npc = self._find_npc(npc_id)
            if npc is None:
                continue
            label = self._format_guest_moment_label(npc_id, snapshot)
            tier = max(0, min(2, npc.temperament))
            reaction = shortage_reactions[tier][npc_id % len(shortage_reactions[tier])]
            texts = tuple(
                template.format(label=label, reaction=reaction)
                for template in shortage_templates
            )
            candidates.append({
                "priority": "high",
                "source": "dining_shortage",
                "npc_ids": [npc_id],
                "texts": texts,
            })

        entertainment_by_npc = {}
        for npc_id, action in completed_actions:
            if action.get("action") in {"paid_entertainment", "free_entertainment"}:
                entertainment_by_npc.setdefault(npc_id, []).append(action)
        for npc_id, actions in entertainment_by_npc.items():
            label = self._format_guest_moment_label(npc_id, snapshot)
            paid_action = next(
                (item for item in actions if item.get("action") == "paid_entertainment"), None
            )
            has_free = any(item.get("action") == "free_entertainment" for item in actions)
            tier = self.ENTERTAINMENT_TIER_OPTIONS.get(
                (paid_action or {}).get("tier_key"), {}
            ).get("display_name")
            entertainment_level = max(
                0,
                min(2, int(self.facilities["entertainment"].level)),
            )
            free_names = "、".join(
                self.FREE_ENTERTAINMENT_NAMES_BY_LEVEL[entertainment_level]
            )
            if paid_action is not None and has_free:
                texts = (
                    f"{label}先体验了{tier or '娱乐项目'}，又去玩了{free_names}。",
                    f"{label}玩完{tier or '娱乐项目'}，又在{free_names}之间转了一圈。",
                    f"{label}先去了{tier or '娱乐项目'}，之后又加入了{free_names}的活动。",
                )
            elif paid_action is not None:
                texts = (
                    f"{label}在娱乐区玩了一会儿，选了{tier or '娱乐项目'}。",
                    f"{label}直奔{tier or '娱乐项目'}，玩得挺投入。",
                    f"{label}在娱乐区转了一圈，最后选了{tier or '娱乐项目'}。",
                    f"{label}跟同伴一起体验了{tier or '娱乐项目'}，笑声不断。",
                )
            else:
                texts = (
                    f"{label}在{free_names}之间玩了一会儿。",
                    f"{label}路过娱乐区时停下来，加入了{free_names}的活动。",
                    f"{label}和别的客人一起玩了会儿{free_names}。",
                    f"{label}在{free_names}之间待了好一会儿才走。",
                    f"{label}被{free_names}的热闹吸引，凑过去玩了一会儿。",
                )
            candidates.append({
                "priority": "normal",
                "source": "entertainment",
                "npc_ids": [npc_id],
                "texts": texts,
            })

        for npc_id, entry in current_entries.items():
            if (
                snapshot.get("arrival_statuses", {}).get(npc_id) == "arrived"
                or entry.get("arrival_status") != "arrived"
            ):
                continue
            label = self._format_guest_moment_label(npc_id, snapshot)
            if not any(action.get("action") == "dining" for action in entry.get("planned_actions", [])):
                candidates.append({
                    "priority": "normal",
                    "source": "no_dining",
                    "npc_ids": [npc_id],
                    "texts": (
                        f"{label}路过篝火厨房时停了停，随后还是回了自己的营位。",
                        f"{label}在篝火厨房前看了一会儿，最后还是回了营位。",
                        f"{label}走到餐台附近看了看菜牌，没点餐就回了营位。",
                        f"{label}在餐饮区门口张望了一下，转身回去了。",
                        f"{label}没进篝火厨房，径直回了自己的营位。",
                        f"{label}经过篝火厨房时停了停，还是先回营位了。",
                        f"{label}在餐台前站了一会儿，又走开了。",
                    ),
                })
            npc = self._find_npc(npc_id)
            if npc is not None and npc.greenery_entry_bonus_applied and self.facilities["greenery"].level > 0:
                candidates.append({
                    "priority": "normal",
                    "source": "greenery",
                    "npc_ids": [npc_id],
                    "texts": (
                        f"{label}进营地时在入口的绿植旁停了一会儿。",
                        f"{label}路过绿植时停下来，多看了一会儿。",
                        f"{label}在营地的绿植边坐了一会儿才起身。",
                        f"{label}经过绿化区时放慢了脚步，看了两眼。",
                        f"{label}在入口的绿植旁站了一会儿，才继续往里走。",
                        f"{label}在绿化带旁歇了歇脚。",
                    ),
                })

        converted_npcs = [
            npc for npc_id, before in snapshot.get("npcs", {}).items()
            if before.get("visit_type") == "day" and not before.get("has_left")
            for npc in [self._find_npc(npc_id)]
            if npc is not None and npc.visit_type == "overnight"
            and self._tent_id_from_location(npc.location) is not None
        ]
        for npc in converted_npcs:
            tent_id = self._tent_id_from_location(npc.location)
            label = self._format_guest_moment_label(npc.id, snapshot)
            candidates.append({
                "priority": "high",
                "source": "day_to_overnight",
                "npc_ids": [npc.id],
                "texts": (
                    f"{label}决定多留一晚，正往{tent_id}号帐篷方向收拾东西。",
                    f"{label}决定多留一晚，正往{tent_id}号帐篷搬行李。",
                    f"{label}临时改了主意要住一晚，已经在往{tent_id}号帐篷走了。",
                    f"{label}想多待一天，正把东西往{tent_id}号帐篷里放。",
                    f"{label}决定住下来，慢悠悠地往{tent_id}号帐篷溜达过去。",
                    f"{label}收拾好东西，往{tent_id}号帐篷的方向走去，看起来是打算多住一晚。",
                ),
            })

        if any(
            event.get("day") == day and event.get("turn") == turn
            and event.get("event_type") == "temporary_conflict"
            for event in self.state.event_history
        ):
            candidates.append({
                "priority": "high",
                "source": "temporary_conflict",
                "npc_ids": [],
                "texts": (
                    "刚处理完争执的两组客人各自散开，又回到了原本的活动里。",
                    "争执平息后，两组客人各自走开，营地又恢复了安静。",
                    "调解之后，那两组客人没再碰面，各忙各的去了。",
                    "事情说开后，两组客人隔着一段距离各自活动，气氛缓和了不少。",
                    "处理完争执，两组客人先后散去，看起来都平静了下来。",
                    "小风波过后，两组客人没有再起冲突，各自继续自己的安排。",
                ),
            })
        return candidates

    def _record_guest_moment(self, snapshot: dict, day: int, turn: int) -> None:
        """在正式经营播报之后，低频落盘一条独立客人剧情镜头。"""
        moments_today = [
            event for event in self.state.event_history
            if event.get("day") == day and event.get("event_type") == "guest_moment"
        ]
        if len(moments_today) >= 2 or any(event.get("turn") == turn for event in moments_today):
            return
        candidates = self._build_guest_moment_candidates(snapshot, day, turn)
        if not candidates:
            return
        rng = random.Random()
        if rng.random() >= self.GUEST_MOMENT_CHANCE:
            return
        high_priority = [item for item in candidates if item["priority"] == "high"]
        selected = rng.choice(high_priority or candidates)
        self._append_event_history(
            day,
            turn,
            rng.choice(selected["texts"]),
            "world",
            "guest_moment",
            selected["npc_ids"],
            {"source": selected["source"]},
        )

    def advance_turn(self) -> dict:
        """推进一个回合，返回结算后的真实状态"""
        executed_day = self.state.day
        executed_turn = self.state.turn
        result = {
            "events": [],
            "next_actions": []
        }
        business_snapshot = (
            self._snapshot_turn_business_state()
            if self.state.turn <= 5
            else None
        )

        # 修复：将已有 today_events 加入本次事件并清空，避免重复展示
        result["events"].extend(self.state.today_events)
        self.state.today_events.clear()

        if self.state.turn <= 5:
            if not self._require_turn_plan_for_advance(result):
                result["day"] = self.state.day
                result["turn"] = self.state.turn
                result["income"] = dict(self.state.today_income)
                result["balance"] = self.state.balance
                result["average_rating"] = self.get_average_rating()
                result["tents"] = self._get_tents_summary()
                result["npcs"] = self._get_npcs_summary()
                return result

            self._process_business_turn(result)
            self._expire_breakdown_repair_windows()
            self._apply_temporary_conflict_event(result)
            self._process_dining(result)
            self._process_entertainment(result)
            self._process_hot_spring(result)
            self._process_nature_observation_plans(result)
            if self.state.turn == 5:
                self._settle_tips(result)
                self._process_day_guest_departures(result)
            if self.state.turn == 4:
                self._process_day_to_overnight(result)
            self._handle_breakdowns(result)
            self._append_turn_business_summaries(
                business_snapshot, executed_day, executed_turn
            )
            self._record_guest_moment(
                business_snapshot, executed_day, executed_turn
            )
            if self.state.turn == 5:
                for npc in self.npc_pool:
                    if npc.has_left or npc.visit_type != "overnight":
                        continue
                    tent = self._find_occupied_tent_for_npc(npc.id)
                    if tent is not None:
                        npc.location = f"tent_{tent.id}"
                discarded_food = self.state.food_stock
                if discarded_food > 0:
                    discard_event = f"今日营业结束，剩余{discarded_food}份食材已作废。"
                    result["events"].append(discard_event)
                    self._record_business_event(
                        executed_day, executed_turn, "food_discard",
                        data={"portions": discarded_food}, merge=False,
                    )
                self.state.food_stock = 0
                self._settle_daily_operating_costs()
            # 推进到下一回合
            self.state.turn += 1
            self._emit_breakdown_repair_window_feedback(result)
            self._emit_pending_breakdown_complaints(result)
            self._restore_active_npc_base_locations()
            self._settle_current_turn_arrivals()
            self._record_current_turn_dining_shortage_preview()
        else:
            if self.state.day_end_completed:
                result["events"].append("日终清单已完成，请调用 start_next_day 开启下一天")
            else:
                result["events"].append("请先提交日终经营清单（day_end_actions）")

        # 在所有结算完成后，重新获取最新状态
        result["day"] = self.state.day
        result["turn"] = self.state.turn
        result["income"] = dict(self.state.today_income)
        result["balance"] = self.state.balance
        result["average_rating"] = self.get_average_rating()
        result["tents"] = self._get_tents_summary()
        result["npcs"] = self._get_npcs_summary()

        return result

    def _settle_daily_operating_costs(self) -> None:
        """在 Turn 5 结束、进入 Turn 6 前结算当天已发生的经营耗损。"""
        lodging_cost = 0
        for entry in self.state.today_arrival_plan:
            if (
                entry.get("planned_day") != self.state.day
                or entry.get("arrival_status") != "arrived"
            ):
                continue
            npc = self._find_npc(entry.get("npc_id"))
            if npc is None or npc.has_left or npc.visit_type != "overnight":
                continue
            tent = self._find_occupied_tent_for_npc(npc.id)
            if tent is None:
                continue
            lodging_cost += int(self.TENT_PRICES[tent.id] * 0.10)

        hot_spring_cost = 0
        if self.state.hot_spring_built:
            hot_spring_cost = int(
                100 + self.state.today_income["hot_spring"] * 0.20
            )

        total_cost = lodging_cost + hot_spring_cost
        if total_cost:
            self.state.balance -= total_cost
        self.state.today_expenses["lodging_consumables"] += lodging_cost
        self.state.today_expenses["hot_spring_operating"] += hot_spring_cost

    def _process_business_turn(self, result: dict):
        """处理营业回合"""
        turn = self.state.turn
        self._restore_active_npc_base_locations()

        if turn == 1:
            self._process_checkout_partial(result)

        elif turn == 2:
            self._process_checkout_all(result)
            deferred_service_actions = self._execute_pending_turn_plan(
                result, defer_improve_service=True
            )
            self._assign_reserved_tents_for_today()
            self._process_checkin(result)
            self._execute_deferred_improve_service_actions(
                result, deferred_service_actions
            )

        elif turn == 3:
            deferred_service_actions = self._execute_pending_turn_plan(
                result, defer_improve_service=True
            )
            self._process_checkin(result)
            self._execute_deferred_improve_service_actions(
                result, deferred_service_actions
            )

        elif turn == 4:
            deferred_service_actions = self._execute_pending_turn_plan(
                result, defer_improve_service=True
            )
            self._process_checkin(result)
            self._execute_deferred_improve_service_actions(
                result, deferred_service_actions
            )

        elif turn == 5:
            self._execute_pending_turn_plan(result)

        # 清理已离开的NPC
        self._cleanup_left_npcs()

    def _process_nature_observation_plans(self, result: Optional[dict] = None) -> None:
        """执行本 Turn 已锁定的自然观察结果，并只公开已完成事实。"""
        for entry in self.state.today_arrival_plan:
            if entry.get("planned_day") != self.state.day:
                continue
            observation_plan = entry.get("observation_plan")
            if (
                not isinstance(observation_plan, dict)
                or observation_plan.get("status") != "pending"
                or observation_plan.get("planned_turn") != self.state.turn
            ):
                continue

            npc = self._find_npc(entry.get("npc_id"))
            if (
                entry.get("arrival_status") != "arrived"
                or npc is None
                or npc.has_left
            ):
                observation_plan["status"] = "skipped"
                continue

            observation_result = observation_plan.get("result")
            insects_by_id = {insect["id"]: insect for insect in self.INSECT_CATALOG}
            insect = insects_by_id.get(observation_result)
            is_new_discovery = bool(
                insect and observation_result not in self.state.discovered_insects
            )
            previous_balance = self.state.balance
            previous_income = self.state.today_income.get("nature_observation", 0)
            previous_discovered = list(self.state.discovered_insects)
            history_length = len(self.state.event_history)
            try:
                observation_plan["status"] = "completed"
                self.state.balance += 20
                self.state.today_income["nature_observation"] = previous_income + 20
                if is_new_discovery:
                    self.state.discovered_insects = self._normalize_discovered_insects(
                        previous_discovered + [observation_result]
                    )
                event_data = {
                    "income": 20,
                    "result": (
                        observation_result
                        if observation_result == "not_found" or insect
                        else "not_found"
                    ),
                    "is_new_discovery": is_new_discovery,
                }
                discovered_count = len(self.state.discovered_insects)
                if is_new_discovery and discovered_count in (3, 6, 9):
                    event_data["observation_ability_unlocked"] = True
                if insect:
                    event_data.update({
                        "insect_id": insect["id"],
                        "insect_name": insect["name"],
                        "rarity": insect["rarity"],
                    })
                self._record_business_event(
                    self.state.day,
                    self.state.turn,
                    "nature_observation",
                    guest_ids=[entry.get("npc_id")],
                    data=event_data,
                    merge=False,
                )
                if result is not None and self.state.event_history:
                    result.setdefault("events", []).append(
                        self.state.event_history[-1]["text"]
                    )
                if is_new_discovery:
                    self._unlock_insect_discovery_achievements()
            except Exception:
                observation_plan["status"] = "pending"
                self.state.balance = previous_balance
                self.state.today_income["nature_observation"] = previous_income
                self.state.discovered_insects = previous_discovered
                del self.state.event_history[history_length:]
                raise

    def _restore_active_npc_base_locations(self):
        """在本 Turn 行为执行前恢复仍在场 NPC 的基础位置。"""
        for npc in self.npc_pool:
            if npc.has_left:
                continue
            if npc.visit_type == "day":
                if (
                    isinstance(npc.campsite_slot, int)
                    and 1 <= npc.campsite_slot <= self.DAY_CAMPSITE_CAPACITY
                ):
                    npc.location = "campsite"
                continue
            if npc.visit_type == "overnight":
                tent = self._find_occupied_tent_for_npc(npc.id)
                if tent is not None:
                    npc.location = f"tent_{tent.id}"

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
        tent = self._find_occupied_tent_for_npc(npc.id)
        if tent is None:
            return
        tent_id = tent.id
        was_broken = tent.status == "broken"
        # 修复：故障帐篷退房后保持 broken，必须经过 repair_tent() 才能恢复使用
        if was_broken:
            tent.status = "broken"
        else:
            tent.status = "cleaning"
        tent.needs_cleaning = True
        tent.occupied_by = None
        npc.location = "leaving"
        npc.has_left = True

        result["events"].append(f"帐篷{tent_id}号客人退房")
        self._try_leave_review(npc, result)

    # -------------------------------------------------------------------------
    # 入住与日间客
    # -------------------------------------------------------------------------

    def _process_checkin(self, result: dict):
        """处理入住"""
        if self.state.turn not in [2, 3, 4]:
            return
        self._process_planned_arrivals(result)
        return

    def _checkin_npc(self, npc: NPCGroup, tent_id: int, result: dict, charge: bool = True):
        """NPC入住帐篷"""
        tent = self.tents[tent_id]
        was_broken = tent.status == "broken"
        if not was_broken:
            tent.status = "occupied"
        tent.occupied_by = npc.id
        npc.location = f"tent_{tent_id}"
        npc.arrival_turn = self.state.turn
        # 正常计划客：直接采用到达计划 entry 中已确定的退房 Turn，不得重新随机。
        planned_checkout_turn = self._get_planned_checkout_turn(npc.id)
        if planned_checkout_turn is not None:
            npc.checkout_turn = planned_checkout_turn
        else:
            # 仅对无到达计划 entry 的非正常链路（如测试直接构造）保留防御兜底。
            self._ensure_checkout_turn(npc)

        if charge:
            income = self.TENT_PRICES[tent_id]
            self.state.balance += income
            self.state.today_income["accommodation"] += income

        self._apply_greenery_entry_bonus_once(npc)

        if was_broken:
            self._apply_broken_penalty(npc)

        if npc not in self.npc_pool:
            self.npc_pool.append(npc)
        self._record_served_group_once(npc)
        self._unlock_achievement("first_overnight_group")
        if was_broken:
            result["events"].append(
                self._get_temperament_service_reaction(npc, "tent_broken")
            )
        result["events"].append(f"一组{npc.group_size}人入住{tent_id}号帐篷")

    def _apply_greenery_entry_bonus_once(self, npc: NPCGroup):
        """客组首次成功入场时结算一次绿化加成"""
        if npc.greenery_entry_bonus_applied:
            return
        self.apply_satisfaction_delta(
            npc, self.facilities["greenery"].greenery_satisfaction
        )
        npc.greenery_entry_bonus_applied = True

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

                menu_key = action.get("menu_key")
                if menu_key not in self.DINING_SET_MENUS:
                    action["status"] = "skipped"
                    action["result"] = "invalid_menu"
                    continue
                menu = self.DINING_SET_MENUS[menu_key]
                required_portions = npc.group_size
                current_stock = self.state.food_stock

                if current_stock < required_portions:
                    action["status"] = "waiting_for_restock"
                    action["result"] = "insufficient_food"
                    npc.had_food_shortage = True
                    if not action.get("food_shortage_penalty_applied", False):
                        self.apply_satisfaction_delta(npc, -4)
                        action["food_shortage_penalty_applied"] = True
                    reaction = self._get_temperament_service_reaction(
                        npc, "insufficient_food"
                    )
                    result["events"].append(
                        f"{npc.group_size}人客人想在餐饮区用餐，但食材不足："
                        f"需要{required_portions}份，当前只有{current_stock}份。{reaction}"
                    )
                    self._record_dining_shortage_if_new([npc.id])
                    continue

                spend = menu["price_per_person"] * npc.group_size
                if spend <= 0:
                    action["status"] = "skipped"
                    action["result"] = "invalid_spend"
                    continue

                self._complete_dining_action(npc, action, menu, spend, result)
        return

    def _record_dining_shortage_if_new(self, guest_ids: list[int]) -> None:
        for event in self.state.event_history:
            if (
                event.get("day") == self.state.day
                and event.get("turn") == self.state.turn
                and event.get("event_type") == "dining_shortage"
                and set(guest_ids).issubset(set(event.get("guest_ids", [])) )
            ):
                return
        self._record_business_event(
            self.state.day, self.state.turn, "dining_shortage",
            guest_ids=guest_ids, merge=False,
        )

    def _get_temperament_service_reaction(
        self, npc: NPCGroup, failure_type: str
    ) -> str:
        reactions = {
            "insufficient_food": ("客人表示理解，愿意稍等补货。", "客人有些失望，决定先等等。", "客人明显不满，催促尽快补货。"),
            "campsite_full": ("客人表示理解，只能遗憾离开。", "客人有些失望，只能先离开。", "客人明显不满，抱怨着离开。"),
            "tent_unavailable": ("客人表示理解，只能遗憾离开。", "客人有些失望，只能先离开。", "客人明显不满，抱怨着离开。"),
            "hot_spring_full": ("客人表示遗憾，愿意下次再试。", "客人有些失望，只能放弃泡汤。", "客人明显不满，抱怨温泉容量太小。"),
            "tent_broken": ("客人表示理解，愿意等候维修。", "客人有些失望，希望尽快修好。", "客人明显不满，催促尽快维修。"),
        }
        failure_reactions = reactions.get(failure_type)
        if failure_reactions is None:
            return ""
        return failure_reactions[npc.temperament]

    def _complete_dining_action(
        self,
        npc: NPCGroup,
        action: dict,
        menu: dict,
        spend: int,
        result: dict,
    ):
        npc.location = "dining"
        self.state.food_stock -= npc.group_size
        self.state.balance += spend
        self.state.today_income["dining"] += spend
        self.apply_satisfaction_delta(npc, menu["satisfaction_gain"])
        self._mark_dining_consumed(npc)
        self._record_successful_dining_once(npc)
        action["status"] = "completed"
        action["result"] = "success"
        action["charged_amount"] = spend
        action["food_portions_used"] = npc.group_size
        action["satisfaction_gain"] = menu["satisfaction_gain"]
        self._record_business_event(
            self.state.day, self.state.turn, "dining_completed", guest_ids=[npc.id],
            data={
                "income": spend,
                "food_portions": npc.group_size,
                "satisfaction_gain": menu["satisfaction_gain"],
            },
        )
        result["events"].append(
            f"1组客人购买{menu['display_name']}，"
            f"{npc.group_size}人用餐，收入+{spend}，"
            f"消耗食材{npc.group_size}份，"
            f"整组满意度+{menu['satisfaction_gain']}"
        )

    def _retry_waiting_dining_after_restock(self, result: dict):
        waiting_actions = []
        for entry_index, entry in enumerate(self.state.today_arrival_plan):
            if entry.get("planned_day") != self.state.day:
                continue
            for action_index, action in enumerate(entry.get("planned_actions", [])):
                if action.get("action") != "dining":
                    continue
                if action.get("status") != "waiting_for_restock":
                    continue
                waiting_actions.append(
                    (
                        action.get("planned_turn", 0),
                        entry_index,
                        action_index,
                        entry,
                        action,
                    )
                )

        waiting_actions.sort(key=lambda item: (item[0], item[1], item[2]))

        for _, _, _, entry, action in waiting_actions:
            npc = self._find_npc(entry["npc_id"])
            if npc is None or npc.has_left or self._has_consumed_dining_today(npc):
                continue

            menu_key = action.get("menu_key")
            if menu_key not in self.DINING_SET_MENUS:
                continue

            menu = self.DINING_SET_MENUS[menu_key]
            spend = menu["price_per_person"] * npc.group_size
            if spend <= 0:
                continue
            if self.state.food_stock < npc.group_size:
                continue

            self._complete_dining_action(npc, action, menu, spend, result)

    def _process_hot_spring(self, result: dict):
        for entry in self.state.today_arrival_plan:
            if entry.get("planned_day") != self.state.day:
                continue
            for action in entry.get("planned_actions", []):
                if action.get("action") != "hot_spring":
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
                if not self.state.hot_spring_built:
                    action["status"] = "skipped"
                    action["result"] = "hot_spring_unavailable"
                    continue

                remaining_capacity = (
                    self.HOT_SPRING_DAILY_CAPACITY
                    - self.state.hot_spring_people_served_today
                )
                if remaining_capacity < npc.group_size:
                    action["status"] = "failed"
                    action["result"] = "capacity_full"
                    action["charged_amount"] = 0
                    action["satisfaction_gain"] = 0
                    action["people_served"] = 0
                    result["events"].append(
                        f"一组{npc.group_size}人的温泉需求失败：剩余容量{remaining_capacity}人，容量不足，未收费。"
                        f"{self._get_temperament_service_reaction(npc, 'hot_spring_full')}"
                    )
                    continue

                action_before = dict(action)
                balance_before = self.state.balance
                income_before = self.state.today_income["hot_spring"]
                people_before = self.state.hot_spring_people_served_today
                location_before = npc.location
                satisfaction_before = (
                    npc.total_satisfaction,
                    npc.positive_experience_total,
                    npc.negative_experience_total,
                )
                try:
                    spend = self.HOT_SPRING_PRICE_PER_PERSON * npc.group_size
                    npc.location = "hot_spring"
                    self.state.balance += spend
                    self.state.today_income["hot_spring"] += spend
                    self.state.hot_spring_people_served_today += npc.group_size
                    self.apply_satisfaction_delta(
                        npc, self.HOT_SPRING_SATISFACTION_GAIN
                    )
                    action["status"] = "completed"
                    action["result"] = "success"
                    action["charged_amount"] = spend
                    action["satisfaction_gain"] = self.HOT_SPRING_SATISFACTION_GAIN
                    self._record_business_event(
                        self.state.day, self.state.turn, "hot_spring_completed",
                        guest_ids=[npc.id],
                        data={
                            "income": spend,
                            "satisfaction_gain": self.HOT_SPRING_SATISFACTION_GAIN,
                        },
                    )
                    action["people_served"] = npc.group_size
                    result["events"].append(
                        f"1组客人使用温泉，收入+{spend}，整组满意度+{self.HOT_SPRING_SATISFACTION_GAIN}。"
                    )
                except Exception:
                    self.state.balance = balance_before
                    self.state.today_income["hot_spring"] = income_before
                    self.state.hot_spring_people_served_today = people_before
                    npc.location = location_before
                    (
                        npc.total_satisfaction,
                        npc.positive_experience_total,
                        npc.negative_experience_total,
                    ) = satisfaction_before
                    action.clear()
                    action.update(action_before)
                    action["status"] = "failed"
                    action["result"] = "execution_error"

    def _process_entertainment(self, result: dict):
        """处理娱乐消费"""
        for entry in self.state.today_arrival_plan:
            if entry.get("planned_day") != self.state.day:
                continue
            for action in entry.get("planned_actions", []):
                action_name = action.get("action")
                if action_name not in ("paid_entertainment", "free_entertainment"):
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

                if action_name == "paid_entertainment":
                    tier_key = action.get("tier_key")
                    if tier_key not in self.ENTERTAINMENT_TIER_OPTIONS:
                        action["status"] = "skipped"
                        action["result"] = "invalid_tier"
                        continue

                    tier = self.ENTERTAINMENT_TIER_OPTIONS[tier_key]
                    spend = tier["price_per_group"]
                    if spend < 0:
                        action["status"] = "skipped"
                        action["result"] = "invalid_spend"
                        continue

                    npc.location = "entertainment"
                    self.state.balance += spend
                    self.state.today_income["entertainment"] += spend
                    self.apply_satisfaction_delta(npc, tier["satisfaction_gain"])
                    action["status"] = "completed"
                    action["result"] = "success"
                    action["charged_amount"] = spend
                    action["satisfaction_gain"] = tier["satisfaction_gain"]
                    self._record_successful_paid_entertainment_once(npc)
                    result["events"].append(
                        f"1组客人参加{tier['display_name']}，"
                        f"整组收费+{spend}，"
                        f"整组满意度+{tier['satisfaction_gain']}"
                    )
                    continue

                npc.location = "entertainment"
                self.apply_satisfaction_delta(npc, 1)
                action["status"] = "completed"
                action["result"] = "success"
                action["charged_amount"] = 0
                action["satisfaction_gain"] = 1
                result["events"].append(
                    "1组客人参加免费娱乐，收入+0，整组满意度+1"
                )

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

    def _assign_reserved_tents_for_today(self):
        """将今日 arrival plan 中尚未到店的预约帐篷标记为 reserved。"""
        for entry in self.state.today_arrival_plan:
            if (
                entry.get("planned_day") != self.state.day
                or entry.get("source") != "reservation"
                or entry.get("visit_type") != "overnight"
                or entry.get("arrival_status") != "pending"
            ):
                continue
            tent_id = entry.get("tent_id")
            if tent_id not in self.tents:
                continue
            tent = self.tents[tent_id]
            if not self._is_tent_unlocked(tent):
                continue
            if tent.status == "available":
                tent.status = "reserved"
            elif tent.status == "cleaning":
                pass  # 等清洁完自动变 reserved

    def _leave_day_guest(self, npc: NPCGroup, result: dict):
        """日间游客离场并触发评价。"""
        self._record_business_event(
            self.state.day, self.state.turn, "day_departure",
            guest_ids=[npc.id], data={"count": 1},
        )
        npc.has_left = True
        npc.location = "leaving"
        npc.campsite_slot = None
        self._try_leave_review(npc, result)

    def _process_day_to_overnight(self, result: dict):
        """Turn 4 营业结束后，按当天计划包统一结算日转夜。"""
        intent_npc_ids = {
            entry["npc_id"]
            for entry in self.state.today_arrival_plan
            if entry.get("planned_day") == self.state.day
            and entry.get("visit_type") == "day"
            and entry.get("day_to_overnight_intent") is True
        }
        day_guests = [
            npc for npc in self.npc_pool
            if npc.visit_type == "day"
        ]
        candidate_guests = [
            npc for npc in day_guests if npc.id in intent_npc_ids
        ]
        available_tents = [
            tent for tent in self.tents.values()
            if self._is_tent_unlocked(tent)
            and (tent.status == "available"
                 or (tent.status == "broken" and tent.occupied_by is None))
        ]
        matches = self._match_day_to_overnight_tents(
            candidate_guests, available_tents
        )

        for guest in candidate_guests:
            tent_id = matches.get(guest.id)
            if tent_id is None:
                continue
            tent = self.tents[tent_id]
            was_broken = tent.status == "broken"
            if not was_broken:
                tent.status = "occupied"
            tent.occupied_by = guest.id
            guest.location = f"tent_{tent_id}"
            guest.visit_type = "overnight"
            guest.campsite_slot = None
            guest.checkout_turn = 1
            if was_broken:
                self._apply_broken_penalty(guest)
            income = self.TENT_PRICES[tent_id]
            self.state.balance += income
            self.state.today_income["accommodation"] += income

        if not candidate_guests:
            return
        matched_tent_ids = [matches[guest.id] for guest in candidate_guests if guest.id in matches]
        successful_count = len(matched_tent_ids)
        total_count = len(candidate_guests)
        if successful_count:
            self._unlock_achievement("first_day_to_overnight")
        tent_text = "、".join(f"{tent_id}号" for tent_id in matched_tent_ids)
        if successful_count == total_count:
            if total_count == 1:
                result["events"].append(
                    f"傍晚，有1组日间客决定留下过夜，已入住{tent_text}帐篷。"
                )
            else:
                result["events"].append(
                    f"傍晚，有{total_count}组日间客决定留下过夜，已分别入住{tent_text}帐篷。"
                )
        elif successful_count:
            failed_count = total_count - successful_count
            failed_reactions = " ".join(
                self._get_temperament_service_reaction(guest, "tent_unavailable")
                for guest in candidate_guests
                if guest.id not in matches
            )
            failed_text = (
                "另一组未能转为过夜客，将继续参与 Turn 5 日间活动，并在 Turn 5 活动结束后离场。"
                if failed_count == 1
                else f"另外{failed_count}组未能转为过夜客，将继续参与 Turn 5 日间活动，并在 Turn 5 活动结束后离场。"
            )
            result["events"].append(
                f"傍晚，有{total_count}组日间客决定留下过夜，其中{successful_count}组入住了{tent_text}帐篷；{failed_text}{failed_reactions}"
            )
        else:
            failed_reactions = " ".join(
                self._get_temperament_service_reaction(guest, "tent_unavailable")
                for guest in candidate_guests
            )
            result["events"].append(
                f"傍晚，有{total_count}组日间客决定留下过夜，但未能转为过夜客，将继续参与 Turn 5 日间活动，并在 Turn 5 活动结束后离场。{failed_reactions}"
            )

    # -------------------------------------------------------------------------
    # 帐篷故障
    # -------------------------------------------------------------------------

    def _process_day_guest_departures(self, result: dict):
        """Turn 5 普通计划活动完成后，统一处理仍为日间客的离场。"""
        for guest in self.npc_pool:
            if guest.visit_type == "day" and not guest.has_left:
                self._leave_day_guest(guest, result)
        self._cleanup_left_npcs()

    def _handle_breakdowns(self, result: dict):
        """处理帐篷故障"""
        current_turn = self._absolute_turn()
        new_breakdown_count = 0
        for tent_id, tent in self.tents.items():
            if not self._is_tent_unlocked(tent):
                continue
            if (tent.status in ["occupied", "available", "reserved"]
                    and current_turn >= tent.next_breakdown_turn
                    and tent.next_breakdown_turn > 0):
                tent.status = "broken"
                new_breakdown_count += 1
                occupant = None
                # 修复：保留 occupied_by，不移动住客
                if tent.occupied_by is not None:
                    occupant = next(
                        (n for n in self.npc_pool
                         if n.id == tent.occupied_by and not n.has_left),
                        None,
                    )
                    self._apply_broken_penalty(occupant)
                tent.breakdown_repair_state = {
                    "deadline_day": self.state.day,
                    "deadline_turn": 6 if self.state.turn == 5 else self.state.turn + 1,
                    "timely": True,
                    "complaint_pending": False,
                }
                if occupant is not None:
                    result["events"].append(
                        f"{tent_id}号帐篷突发故障，住客明显不满。及时维修可以挽回。"
                    )
                else:
                    result["events"].append(
                        f"{tent_id}号帐篷突发故障。及时维修可以避免影响后续住客。"
                    )
                result["next_actions"].append({
                    "action": "repair_tent",
                    "params": {"tent_id": tent_id},
                })
        if new_breakdown_count >= 2:
            self._unlock_achievement("bad_luck_breakdowns")

    def _is_timely_breakdown_repair(self, tent: Tent) -> bool:
        """仅在本次故障的第一个可维修窗口内允许挽回体验扣分。"""
        state = tent.breakdown_repair_state
        return bool(
            isinstance(state, dict)
            and state.get("timely")
            and state.get("deadline_day") == self.state.day
            and state.get("deadline_turn") == self.state.turn
        )

    def _expire_breakdown_repair_windows(self) -> None:
        """当前维修窗口结束后，将未及时处理的故障标记为不可挽回。"""
        for tent in self._get_unlocked_tents():
            state = tent.breakdown_repair_state
            if not isinstance(state, dict) or not state.get("timely"):
                continue
            if (
                state.get("deadline_day") == self.state.day
                and state.get("deadline_turn") == self.state.turn
            ):
                state["timely"] = False
                state["complaint_pending"] = tent.occupied_by is not None

    def _emit_breakdown_repair_window_feedback(self, result: dict) -> None:
        """首个维修轮次开始时，说明及时维修是否仍可执行。"""
        if self.state.turn > 5 or self.state.decisions_left > 0:
            return
        for tent in self._get_unlocked_tents():
            if self._is_timely_breakdown_repair(tent):
                result["events"].append(
                    f"今日决策点已用尽，{tent.id}号帐篷无法及时维修，"
                    "住客的不满未能挽回。"
                )

    def _emit_pending_breakdown_complaints(self, result: dict) -> None:
        """错过及时维修窗口后，下一轮只提示一次仍在场住客的投诉。"""
        for tent in self._get_unlocked_tents():
            state = tent.breakdown_repair_state
            if (
                not isinstance(state, dict)
                or not state.get("complaint_pending")
            ):
                continue
            state["complaint_pending"] = False
            occupant = next(
                (npc for npc in self.npc_pool
                 if npc.id == tent.occupied_by and not npc.has_left),
                None,
            )
            if occupant is not None:
                result["events"].append(
                    f"{tent.id}号帐篷未能及时维修，住客提出投诉。"
                )

    def clean_tents(self, tent_ids: Optional[list[int]] = None) -> dict:
        """AI主动清洁帐篷。不消耗决策点，支持批量清洁。

        Args:
            tent_ids: 要清洁的帐篷ID列表。为None时清洁所有待清洁帐篷。

        Returns:
            {"success": bool, "message": str, "cleaned_tent_ids": list[int]}
        """
        if self.state.day_end_completed:
            return {
                "success": False,
                "message": "日终清单已完成，请开启下一天",
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
            tent.needs_cleaning = False
            cleaned.append(tid)

        return {
            "success": True,
            "message": f"已清洁{cleaned}号帐篷",
            "cleaned_tent_ids": cleaned
        }

    def get_waiting_cleaning_checkin_tent_ids(self) -> list[int]:
        """返回本 Turn 已到达、正等待清洁帐篷的预约客目标。"""
        waiting_tent_ids = set()
        for entry in self.state.today_arrival_plan:
            if (
                entry.get("planned_day") != self.state.day
                or entry.get("arrival_turn") != self.state.turn
                or entry.get("arrival_status") != "pending"
                or entry.get("source") != "reservation"
                or entry.get("visit_type") != "overnight"
            ):
                continue
            tent_id = entry.get("tent_id")
            tent = self.tents.get(tent_id)
            if tent is not None and (tent.needs_cleaning or tent.status == "cleaning"):
                waiting_tent_ids.add(tent.id)
        return sorted(waiting_tent_ids)

    def get_turn6_decision_summary(self) -> dict:
        """返回只影响当前日终选择的紧凑摘要。"""
        income_total = sum(self.state.today_income.values())
        expense_total = sum(self.state.today_expenses.values())
        summary = {
            "today_net_income": income_total - expense_total,
            "balance": self.state.balance,
            "debt_remaining": self.state.debt_remaining,
            "repayment_deadline_day": self.STARTUP_DEBT_SETTLEMENT_DAY,
        }
        broken_tents = [
            tent.id for tent in self._get_unlocked_tents()
            if tent.status == "broken"
        ]
        if broken_tents:
            summary["broken_tents"] = [
                {"tent_id": tent_id, "repair_cost": self.REPAIR_COST}
                for tent_id in broken_tents
            ]
        cleaning_tents = [
            tent.id for tent in self._get_unlocked_tents()
            if tent.needs_cleaning or tent.status == "cleaning"
        ]
        if cleaning_tents:
            summary["cleaning_tent_ids"] = cleaning_tents
        discarded_food = sum(
            int(event.get("data", {}).get("portions", 0) or 0)
            for event in self.state.event_history
            if event.get("day") == self.state.day
            and event.get("event_type") == "food_discard"
        )
        if discarded_food:
            summary["food_discarded_portions"] = discarded_food
            summary["alerts"] = [
                f"营业已结束，剩余{discarded_food}份食材已废弃。"
            ]
        return summary

    # -------------------------------------------------------------------------
    # NPC清理
    # -------------------------------------------------------------------------

    def _cleanup_left_npcs(self):
        """移除所有已离开的 NPC。"""
        self.npc_pool = [n for n in self.npc_pool if not n.has_left]

    # -------------------------------------------------------------------------
    # 日终管理（修复 #5：绿化重复衰减）
    # -------------------------------------------------------------------------

    def _process_day_end(self, result: dict):
        """日终管理阶段提示事件"""
        result["events"].append("=== 日终管理阶段 ===")
        result["phase"] = "management"

    # 允许出现在日终批处理清单中的动作
    DAY_END_ACTIONS = {
        "repay_debt",
        "repair_tent",
        "clean_tents",
        "manage_greenery",
        "buy_food_package",
        "purchase_growth_project",
    }

    def _day_end_action_summary_label(self, item_result: dict) -> str:
        """返回日终经营汇总使用的玩家可见动作名称。"""
        action_name = item_result.get("action")
        if action_name == "repay_debt":
            return "偿还债务"
        if action_name == "buy_food_package":
            return "补充食材"
        if action_name == "manage_greenery":
            return "打理绿化"
        if action_name == "clean_tents":
            return "清洁帐篷"
        if action_name == "repair_tent":
            tent_id = (item_result.get("params") or {}).get("tent_id")
            return f"维修{tent_id}号帐篷" if tent_id is not None else "维修帐篷"
        if action_name == "purchase_growth_project":
            display_name = item_result.get("display_name") or "成长项目"
            category = item_result.get("category")
            if category in {"tent", "hot_spring", "nature_observation_station"}:
                return f"建设{display_name}"
            return f"升级{display_name}"
        return ""

    def _day_end_action_cost(self, action_data: dict) -> int:
        action_name = action_data.get("action")
        params = action_data.get("params") or {}
        if action_name == "repay_debt":
            amount = params.get("amount")
            return amount if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0 else 0
        if action_name == "repair_tent":
            return self.REPAIR_COST
        if action_name == "manage_greenery":
            return 50 if params.get("action", "maintain") == "maintain" else 0
        if action_name == "buy_food_package":
            return self.FOOD_PACKAGES.get(params.get("package_key"), {}).get("price", 0)
        if action_name == "purchase_growth_project":
            project = next(
                (p for p in self.GROWTH_PROJECT_CATALOG
                 if p["project_id"] == params.get("project_id")),
                None,
            )
            return project.get("price", 0) if project else 0
        return 0

    def submit_day_end_actions(self, actions: Optional[list]) -> dict:
        """日终批处理入口：一次性执行完整经营清单。

        仅 Day N Turn 6 且未完成时可调用；actions 数量不限，空清单合法。
        严格按提交顺序执行，单项失败记录结果并继续，已成功项不回滚。
        全部执行后 day_end_completed=True，仍停留在当前 Day/Turn 6。
        """
        if self.state.turn != 6:
            return {
                "success": False,
                "error_code": "day_end_not_available",
                "message": "日终清单只能在 Turn 6 提交",
            }
        if self.state.day_end_completed:
            return {
                "success": False,
                "error_code": "day_end_already_completed",
                "message": "日终清单已执行完成，请开启下一天",
            }

        actions = [] if actions is None else list(actions)
        food_preorder_count = sum(
            1
            for action_data in actions
            if isinstance(action_data, dict)
            and action_data.get("action") == "buy_food_package"
        )
        if food_preorder_count > 1:
            return {
                "success": False,
                "error_code": "duplicate_food_preorder",
                "message": "日终清单中最多只能选择一个食材包",
            }
        has_greenery_upgrade = any(
            isinstance(action_data, dict)
            and action_data.get("action") == "purchase_growth_project"
            and next(
                (project.get("category") for project in self.GROWTH_PROJECT_CATALOG
                 if project.get("project_id") == (action_data.get("params") or {}).get("project_id")),
                None,
            ) == "greenery"
            for action_data in actions
        )
        submitted_cost = 0
        budgeted_growth_projects = set()
        for action_data in actions:
            if not isinstance(action_data, dict):
                continue
            action_name = action_data.get("action")
            if has_greenery_upgrade and action_name == "manage_greenery":
                continue
            if action_name == "purchase_growth_project":
                project_id = (action_data.get("params") or {}).get("project_id")
                if project_id in budgeted_growth_projects:
                    continue
                budgeted_growth_projects.add(project_id)
                if not next(
                    (
                        project.get("can_purchase_now")
                        for project in self.get_growth_project_catalog()
                        if project.get("project_id") == project_id
                    ),
                    False,
                ):
                    continue
            submitted_cost += self._day_end_action_cost(action_data)
        if submitted_cost > self.state.balance:
            return {
                "success": False,
                "error_code": "day_end_budget_exceeded",
                "balance": self.state.balance,
                "submitted_cost": submitted_cost,
                "shortfall": submitted_cost - self.state.balance,
            }
        result = {
            "success": True,
            "events": [],
            "results": [],
        }
        executed_day = self.state.day
        executed_turn = self.state.turn
        successful_action_labels = []
        total_spend = 0
        self._process_day_end(result)

        ordered_actions = sorted(
            actions,
            key=lambda item: 0 if (
                isinstance(item, dict)
                and item.get("action") == "purchase_growth_project"
                and next(
                    (project.get("category") for project in self.GROWTH_PROJECT_CATALOG
                     if project.get("project_id") == (item.get("params") or {}).get("project_id")),
                    None,
                ) == "greenery"
            ) else 1,
        )
        greenery_upgrade_succeeded = False
        for action_data in ordered_actions:
            if not isinstance(action_data, dict):
                result["results"].append({
                    "action": None,
                    "success": False,
                    "error_code": "invalid_action_data",
                    "message": "动作必须是对象",
                })
                continue

            action_name = action_data.get("action")
            params = action_data.get("params") or {}
            item_result = {
                "action": action_name,
                "params": dict(params),
            }

            if action_name not in self.DAY_END_ACTIONS:
                item_result.update({
                    "success": False,
                    "error_code": "day_end_action_not_allowed",
                    "message": f"动作 {action_name} 不允许出现在日终清单",
                })
                result["results"].append(item_result)
                continue

            balance_before = self.state.balance
            if action_name == "manage_greenery" and greenery_upgrade_succeeded:
                action_result = {
                    "success": True,
                    "skipped": True,
                    "message": "绿化升级已包含当日维护，本次打理绿化未执行。",
                }
                result["events"].append(action_result["message"])
            elif action_name == "repay_debt":
                action_result = self.repay_debt(params.get("amount"))
            elif action_name == "repair_tent":
                action_result = self.repair_tent(
                    params.get("tent_id"), consume_decision=False
                )
            elif action_name == "clean_tents":
                action_result = self.clean_tents(params.get("tent_ids"))
            elif action_name == "manage_greenery":
                msg = self.manage_greenery(params.get("action", "maintain"))
                action_result = {
                    "success": msg.startswith("绿化已打理"),
                    "message": msg,
                }
            elif action_name == "buy_food_package":
                action_result = self.buy_food_package(params.get("package_key"))
            else:  # purchase_growth_project
                action_result = self.purchase_growth_project(
                    params.get("project_id")
                )

            item_result.update(action_result)
            if not item_result.get("success"):
                if not item_result.get("error_code"):
                    item_result["error_code"] = "day_end_action_failed"
                if not item_result.get("message"):
                    item_result["message"] = "日终行动执行失败"
            result["results"].append(item_result)
            if (
                action_name == "purchase_growth_project"
                and item_result.get("success")
                and item_result.get("category") == "greenery"
            ):
                greenery_upgrade_succeeded = True
            if item_result.get("success"):
                if not item_result.get("skipped"):
                    params = item_result.get("params") or {}
                    targets = []
                    if action_name in {"clean_tents", "repair_tent"}:
                        ids = params.get("tent_ids") if action_name == "clean_tents" else [params.get("tent_id")]
                        targets = [{"type": "tent", "id": tid} for tid in (ids or []) if tid is not None]
                    elif action_name == "manage_greenery":
                        targets = [{"type": "facility", "id": "greenery"}]
                    elif action_name == "buy_food_package":
                        targets = [{"type": "facility", "id": "dining"}]
                    elif action_name == "purchase_growth_project":
                        category = item_result.get("category")
                        targets = [{"type": category, "id": item_result.get("project_id") or params.get("project_id")}]
                    data = {"params": params}
                    if action_name == "purchase_growth_project":
                        data.update({"category": item_result.get("category"), "project_id": item_result.get("project_id") or params.get("project_id")})
                    self._append_event_history(executed_day, executed_turn, item_result.get("message", action_name), "action", action_name, data=data)
                    self.state.event_history[-1].update({"actor": "player", "action": action_name, "targets": targets})
                balance_change = self.state.balance - balance_before
                if not item_result.get("skipped"):
                    successful_action_labels.append(
                        self._day_end_action_summary_label(item_result)
                    )
                if balance_change < 0:
                    total_spend -= balance_change

        successful_action_labels = [
            label for label in successful_action_labels if label
        ]
        if successful_action_labels:
            history_text = f"日终完成：{'、'.join(successful_action_labels)}"
            if total_spend:
                history_text += f"，共支出{total_spend}金币"
            self._append_event_history(
                executed_day, executed_turn, history_text + "。", "action"
            )

        succeeded_count = sum(
            1 for item in result["results"] if item.get("success")
        )
        failed_count = len(result["results"]) - succeeded_count
        if not result["results"]:
            action_execution_status = "no_actions"
        elif failed_count == 0:
            action_execution_status = "all_succeeded"
        elif succeeded_count == 0:
            action_execution_status = "all_failed"
        else:
            action_execution_status = "partial_success"
        result.update({
            "action_execution_status": action_execution_status,
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
        })

        self._finalize_post_reservation(result)

        self._expire_breakdown_repair_windows()
        self.state.day_end_completed = True
        result["day_end_completed"] = True
        self._append_result_events_to_history(
            executed_day, executed_turn, result["events"]
        )
        return result

    def start_next_day(self) -> dict:
        """开启下一天：仅日终清单完成后可调用，确定性跨日推进。"""
        if not self.state.day_end_completed:
            return {
                "success": False,
                "error_code": "day_end_not_completed",
                "message": "请先完成日终清单",
            }

        result = {"events": [], "success": False}
        self._new_day(result)
        self._emit_pending_breakdown_complaints(result)
        self.state.day_end_completed = False
        notifications = self._consume_pending_achievements()
        if notifications:
            result["achievement_notifications"] = notifications
        self._append_result_events_to_history(
            self.state.day, self.state.turn, result["events"]
        )
        self._append_result_events_to_history(
            self.state.day, self.state.turn, self.state.today_events
        )
        self.state.today_events.clear()
        result["success"] = True
        result["day"] = self.state.day
        result["turn"] = self.state.turn
        return result

    def manage_greenery(self, action: str) -> str:
        """绿化管理"""
        if self.state.day_end_completed:
            return "日终清单已完成，请开启下一天"
        # 修复：阶段保护，绿化管理仅限日终管理阶段
        if self.state.turn != 6:
            return "绿化管理只能在日终管理阶段（Turn 6）进行"
        facility = self.facilities["greenery"]
        if self.state.greenery_processed_today:
            return "今天已经处理过绿化了"

        if action == "maintain":
            cost = 50
            if self.state.balance < cost:
                return f"余额不足，需要{cost}金币"
            self.state.balance -= cost
            self.state.today_expenses["greenery"] = self.state.today_expenses.get("greenery", 0) + cost
            facility.greenery_satisfaction = min(
                self.GREENERY_LEVEL_MAX.get(facility.level, 10.0),
                facility.greenery_satisfaction + 1.0,
            )
            self.state.greenery_processed_today = True
            self.state.successful_greenery_maintenance_count += 1
            return f"绿化已打理，花费{cost}金币"

        if facility.level < 2:
            return "绿化未打理"
        return "绿化已达最高级（Lv.2）"

    # -------------------------------------------------------------------------
    # 经营操作
    # -------------------------------------------------------------------------

    def apply_satisfaction_delta(self, npc: NPCGroup, delta: float) -> float:
        """应用一次真实体验变化，并记录实际生效的正、负满意度累计值。"""
        if delta == 0:
            return 0.0
        before = float(npc.total_satisfaction)
        after = round(min(100.0, max(0.0, before + float(delta))), 1)
        actual_delta = round(after - before, 1)
        npc.total_satisfaction = after
        if actual_delta > 0:
            npc.positive_experience_total = round(
                npc.positive_experience_total + actual_delta, 1
            )
        elif actual_delta < 0:
            npc.negative_experience_total = round(
                npc.negative_experience_total + abs(actual_delta), 1
            )
        return actual_delta

    def _apply_broken_penalty(self, npc: NPCGroup) -> None:
        """对住客应用 broken 临时扣分，同一次故障只扣一次并记录实际扣除值。"""
        if npc is None or npc.broken_tent_penalty != 0:
            return
        actual = min(self.TENT_BREAKDOWN_SATISFACTION_PENALTY, npc.total_satisfaction)
        if actual > 0:
            self.apply_satisfaction_delta(npc, -actual)
            npc.broken_tent_penalty = actual
            npc.had_tent_problem = True

    def _restore_broken_penalty(self, npc: Optional[NPCGroup]) -> None:
        """维修成功后恢复已记录的 broken 临时扣分并清零。"""
        if npc is None or npc.broken_tent_penalty <= 0:
            return
        self.apply_satisfaction_delta(npc, npc.broken_tent_penalty)
        npc.broken_tent_penalty = 0

    def repair_tent(self, tent_id: int, *, consume_decision: bool = True) -> dict:
        """维修帐篷"""
        # 修复：先确认目标帐篷存在且确实为 broken
        tent = self.tents.get(tent_id)
        if not tent or not self._is_tent_unlocked(tent) or tent.status != "broken":
            return {"success": False, "message": "帐篷无需维修"}

        if consume_decision and self.state.decisions_left <= 0:
            return {"success": False, "message": "本 Turn 决策点已用完"}

        if self.state.balance < self.REPAIR_COST:
            return {"success": False, "message": "金币不足"}

        timely_repair = self._is_timely_breakdown_repair(tent)

        self.state.balance -= self.REPAIR_COST
        self.state.today_expenses["repair"] = self.state.today_expenses.get("repair", 0) + self.REPAIR_COST

        # 故障与退房后的清洁需求独立：维修不能跳过待清洁。
        if tent.needs_cleaning:
            tent.status = "cleaning"
        elif tent.occupied_by:
            tent.status = "occupied"
        elif self._is_today_reserved_tent(tent_id):
            tent.status = "reserved"
        else:
            tent.status = "available"
        self._set_next_breakdown(tent)
        if consume_decision:
            self.state.decisions_left -= 1

        if tent.occupied_by and timely_repair:
            occupant = next(
                (n for n in self.npc_pool
                 if n.id == tent.occupied_by and not n.has_left),
                None,
            )
            self._restore_broken_penalty(occupant)
        tent.breakdown_repair_state = None

        if timely_repair:
            message = f"{tent_id}号帐篷已及时修复，住客的不满得到解决。"
        else:
            message = f"{tent_id}号帐篷已修复，但由于处理较晚，住客依旧不满。"
        return {"success": True, "message": message}

    def improve_service(self, *, consume_decision: bool = True) -> dict:
        """提升服务"""
        # 修复：阶段保护，提升服务仅限营业回合
        if self.state.turn > 5:
            return {"success": False, "message": "提升服务只能在营业回合（Turn 1-5）进行"}
        if self.state.improve_service_uses_today >= 2:
            return {"success": False, "message": "今日提升服务次数已达到上限"}
        if consume_decision and self.state.decisions_left <= 0:
            return {"success": False, "message": "本 Turn 决策点已用完"}

        if consume_decision:
            self.state.decisions_left -= 1
        self.state.improve_service_uses_today += 1
        affected = []
        targets = []
        for npc in self.npc_pool:
            if not npc.has_left and random.random() < 0.3:
                if not self._guest_received_daily_satisfaction_effect(
                    npc.id, "improve_service"
                ):
                    targets.append(self._npc_replay_target(npc))
                    self.apply_satisfaction_delta(npc, 5)
                    npc.received_service_boost = True
                    affected.append(npc.id)

        if affected:
            labels = [self._visible_guest_label(npc_id) for npc_id in affected]
            message = f"服务提升，{'、'.join(labels)}满意度+5。"
            self._record_business_event(self.state.day, self.state.turn, "improve_service", guest_ids=affected, actor="player", action="improve_service", targets=targets, merge=False)
        else:
            message = f"服务提升，{len(affected)}组客人满意度+5"
            self._record_business_event(self.state.day, self.state.turn, "improve_service", guest_ids=[], actor="player", action="improve_service", targets=[], merge=False)
        return {"success": True, "message": message, "affected_npc_ids": affected, "replay_targets": targets}

    def _active_guest_ids(self) -> list[int]:
        return [npc.id for npc in self.npc_pool if not npc.has_left]

    def _guest_received_daily_satisfaction_effect(
        self, npc_id: int, event_type: str
    ) -> bool:
        """复用当天经营事件中的命中客组，不新增重复的客组状态。"""
        return any(
            event.get("day") == self.state.day
            and event.get("event_type") == event_type
            and npc_id in event.get("guest_ids", [])
            for event in self.state.event_history
        )

    def clean_campsite(self, *, consume_decision: bool = True) -> dict:
        if self.state.turn not in (2, 3, 4, 5):
            return {"success": False, "message": "清洁营地只能在营业回合进行"}
        if consume_decision and self.state.decisions_left <= 0:
            return {"success": False, "message": "本 Turn 决策点已用完"}
        if self.state.clean_campsite_uses_today >= 2:
            return {"success": False, "message": "今日清洁营地次数已达到上限"}
        affected = []
        targets = []
        for npc in self.npc_pool:
            if not npc.has_left and random.random() < 0.7:
                if not self._guest_received_daily_satisfaction_effect(
                    npc.id, "clean_campsite"
                ):
                    targets.append(self._npc_replay_target(npc))
                    self.apply_satisfaction_delta(npc, 2)
                    affected.append(npc.id)
        if consume_decision:
            self.state.decisions_left -= 1
        self.state.clean_campsite_uses_today += 1
        labels = [self._visible_guest_label(npc_id) for npc_id in affected]
        message = (
            f"清洁营地，{'、'.join(labels)}满意度+2。"
            if labels else "清洁营地完成。"
        )
        self._record_business_event(self.state.day, self.state.turn, "clean_campsite", guest_ids=affected, actor="player", action="clean_campsite", targets=targets, merge=False)
        return {"success": True, "message": message, "affected_npc_ids": affected, "replay_targets": targets}

    def make_post(self) -> dict:
        if self.state.turn not in (2, 3, 4, 5):
            return {"success": False, "message": "发帖只能在营业回合进行"}
        if self.state.post_used_today:
            return {"success": False, "message": "今天已经发布过帖子"}
        self.state.post_used_today = True
        if random.random() >= 0.25:
            return {"success": True, "message": "帖子已发布"}
        visit_type = "day" if random.random() < 0.5 else "overnight"
        record = self._create_reservation_record(
            group_size=random.randint(1, 6),
            visit_type=visit_type,
            arrival_day=self.state.day + 1,
            paid=False,
            status="post_pending",
        )
        self.state.pending_post_reservation = record
        return {"success": True, "message": "帖子已发布"}

    def hold_campfire(self, *, consume_decision: bool = True) -> dict:
        if self.state.turn != 4:
            return {"success": False, "message": "篝火只能在 Turn 4 进行"}
        if consume_decision and self.state.decisions_left <= 0:
            return {"success": False, "message": "本 Turn 决策点已用完"}
        affected = []
        targets = []
        for npc in self.npc_pool:
            if not npc.has_left and random.random() < 0.6:
                affected.append(npc.id)
                targets.append(self._npc_replay_target(npc))
        self.state.campfire_affected_npc_ids = affected
        if consume_decision:
            self.state.decisions_left -= 1
        labels = [self._visible_guest_label(npc_id) for npc_id in affected]
        self._record_business_event(self.state.day, self.state.turn, "campfire", guest_ids=affected, actor="player", action="campfire", targets=targets, merge=False)
        return {"success": True, "message": f"篝火进行，{'、'.join(labels)}享受了篝火。" if labels else "篝火进行。"}

    def go_stargazing(self, *, consume_decision: bool = True) -> dict:
        if self.state.turn != 5:
            return {"success": False, "message": "星空只能在 Turn 5 进行"}
        if consume_decision and self.state.decisions_left <= 0:
            return {"success": False, "message": "本 Turn 决策点已用完"}
        affected = []
        targets = []
        for npc in self.npc_pool:
            if not npc.has_left and random.random() < 0.6:
                affected.append(npc.id)
                targets.append(self._npc_replay_target(npc))
        self.state.stargazing_affected_npc_ids = affected
        if consume_decision:
            self.state.decisions_left -= 1
        labels = [self._visible_guest_label(npc_id) for npc_id in affected]
        self._record_business_event(self.state.day, self.state.turn, "stargazing", guest_ids=affected, actor="player", action="stargazing", targets=targets, merge=False)
        return {"success": True, "message": f"星空体验，{'、'.join(labels)}感受了星空。" if labels else "星空体验开始。"}

    def _settle_tips(self, result: dict) -> None:
        if self.state.today_tip_settled:
            return
        total = 0
        campfire_ids = set(self.state.campfire_affected_npc_ids)
        stargazing_ids = set(self.state.stargazing_affected_npc_ids)
        for npc in self.npc_pool:
            if npc.has_left:
                continue
            probability = 0.45 if npc.id in campfire_ids else 0.20
            if random.random() < probability:
                total += 45 if npc.id in stargazing_ids else 20
        self.state.today_income["tip"] += total
        if total:
            self.state.balance += total
            self._unlock_achievement("first_tip")
        self.state.today_tip_settled = True
        if total:
            message = f"今日收到小费 {total} 金币。"
            result["events"].append(message)
            self._record_business_event(
                self.state.day, self.state.turn, "tips", data={"amount": total}, merge=False
            )

    def _finalize_post_reservation(self, result: dict) -> None:
        record = self.state.pending_post_reservation
        if not record:
            return
        target_day = self.state.day + 1
        success = False
        if record["visit_type"] == "day":
            accepted = sum(
                1 for item in self.state.reservations
                if item.get("arrival_day") == target_day
                and item.get("visit_type") == "day"
                and item.get("status") == "accepted"
            )
            success = accepted < self.DAY_CAMPSITE_CAPACITY
            if success:
                self.state.balance += self.CAMPSITE_FEE
                self.state.today_income["campsite"] += self.CAMPSITE_FEE
        else:
            tent = self._find_reservable_overnight_tent(record["group_size"])
            success = tent is not None
            if success:
                record["tent_id"] = tent.id
                self.state.balance += self.TENT_PRICES[tent.id]
                self.state.today_income["accommodation"] += self.TENT_PRICES[tent.id]
        if success:
            record["paid"] = True
            record["status"] = "accepted"
            self.state.reservations.append(record)
            message = "今日发布的帖子带来了一组明日预约。"
        else:
            if record["visit_type"] == "day":
                message = "今日帖子带来一组明日预约请求，但明日日间营位已满，未能接下。"
            else:
                message = "今日帖子带来一组明日预约请求，但没有可接待该客组的空闲帐篷，未能接下。"
        result["events"].append(message)
        self.state.pending_post_reservation = None

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

    def _get_pending_day_reservation_count(self) -> int:
        pending_ids = {
            entry.get("npc_id")
            for entry in self.state.today_arrival_plan
            if entry.get("planned_day") == self.state.day
            and entry.get("source") == "reservation"
            and entry.get("visit_type") == "day"
            and entry.get("arrival_status") == "pending"
            and entry.get("paid") is True
        }
        return len(pending_ids)

    def get_day_campsite_remaining(self) -> int:
        """获取当天剩余可接待的日间客组数。"""
        return max(
            0,
            self.DAY_CAMPSITE_CAPACITY
            - self.state.day_campsite_groups_served
            - self._get_pending_day_reservation_count(),
        )

    def _assign_campsite_slot(self, npc: NPCGroup) -> Optional[int]:
        """为成功入场的日间客分配本次到访固定的地图展示营位。"""
        if isinstance(npc.campsite_slot, int) and 1 <= npc.campsite_slot <= self.DAY_CAMPSITE_CAPACITY:
            return npc.campsite_slot

        occupied_slots = {
            other.campsite_slot
            for other in self.npc_pool
            if (
                other.visit_type == "day"
                and not other.has_left
                and isinstance(other.campsite_slot, int)
                and 1 <= other.campsite_slot <= self.DAY_CAMPSITE_CAPACITY
            )
        }
        available_slots = [
            slot
            for slot in range(1, self.DAY_CAMPSITE_CAPACITY + 1)
            if slot not in occupied_slots
        ]
        if not available_slots:
            return None
        npc.campsite_slot = random.choice(available_slots)
        return npc.campsite_slot

    # -------------------------------------------------------------------------
    # 评价系统
    # -------------------------------------------------------------------------

    def _get_review_comment_candidates(self, npc: NPCGroup) -> tuple[list[str], list[str]]:
        """只从该客组本次已经真实发生的经历中收集可评论事项。"""
        positive = []
        negative = []

        def add_unique(target: list[str], tag: str) -> None:
            if tag not in target:
                target.append(tag)

        entry = self._find_arrival_plan_entry(npc_id=npc.id)
        if entry is not None:
            for action in entry.get("planned_actions", []):
                action_name = action.get("action")
                if action.get("status") == "completed":
                    tag = {
                        "dining": "dining",
                        "paid_entertainment": "paid_entertainment",
                        "free_entertainment": "free_entertainment",
                        "hot_spring": "hot_spring",
                    }.get(action_name)
                    if tag is not None:
                        add_unique(positive, tag)
                if (
                    action_name == "hot_spring"
                    and action.get("result") == "capacity_full"
                ):
                    add_unique(negative, "hot_spring_full")

        if npc.received_service_boost:
            add_unique(positive, "service_boost")
        if npc.greenery_entry_bonus_applied:
            add_unique(positive, "greenery")
        if npc.had_food_shortage:
            add_unique(negative, "food_shortage")
        if npc.had_tent_problem:
            add_unique(negative, "tent_problem")
        return positive, negative

    @staticmethod
    def _sample_review_comment_tags(tags: list[str]) -> list[str]:
        if not tags:
            return []
        count = 1 if len(tags) == 1 else random.choice((1, 2))
        return random.sample(tags, count)

    def _select_review_comment_tags(
        self, positive: list[str], negative: list[str], rating: int
    ) -> list[str]:
        """按星级从真实经历中选 1 至 2 个评论主题。"""
        if rating == 5:
            return self._sample_review_comment_tags(positive) if positive else []
        if rating == 4:
            if positive and negative and random.choice((False, False, True)):
                return [random.choice(positive), random.choice(negative)]
            return self._sample_review_comment_tags(positive or negative)
        if rating == 3:
            if positive and negative:
                return [random.choice(positive), random.choice(negative)]
            return self._sample_review_comment_tags(positive or negative)
        return self._sample_review_comment_tags(negative) if negative else []

    def _build_review_comment(self, npc: NPCGroup, rating: int) -> str:
        positive, negative = self._get_review_comment_candidates(npc)
        tags = self._select_review_comment_tags(positive, negative, rating)
        if not tags:
            return random.choice(self.REVIEW_GENERIC_COMMENTS[rating])

        phrases = [random.choice(self.REVIEW_COMMENT_PHRASES[tag]) for tag in tags]
        tag_types = ["positive" if tag in positive else "negative" for tag in tags]
        if len(phrases) == 1:
            if tag_types[0] == "positive":
                ending = "这次很满意。" if rating == 5 else "整体感觉不错。" if rating == 4 else "整体还算可以。"
            else:
                ending = "影响了这次体验。" if rating <= 2 else "有点可惜。"
            return f"{phrases[0]}，{ending}"

        if tag_types[0] == "negative" and tag_types[1] == "positive":
            phrases.reverse()
            tag_types.reverse()
        if tag_types == ["positive", "positive"]:
            ending = "体验很满意。" if rating == 5 else "整体不错。"
            return f"{phrases[0]}，{phrases[1]}，{ending}"
        if tag_types == ["positive", "negative"]:
            return f"{phrases[0]}，就是{phrases[1]}。"
        return f"{phrases[0]}，{phrases[1]}，有点可惜。"

    def _try_leave_review(self, npc: NPCGroup, result: dict):
        """尝试留评价，并延迟到次日晨间结算。"""
        if npc.review_attempted:
            return

        npc.review_attempted = True
        if random.random() < 0.5:
            rating = self._calculate_rating(self._get_review_score(npc))
            npc.review_rating = rating
            npc.review_left = True
            self.state.pending_reviews.append({
                "created_day": self.state.day,
                "rating": rating,
                "npc_id": npc.id,
                "visit_type": npc.visit_type,
                "group_size": npc.group_size,
                "comment": self._build_review_comment(npc, rating),
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

        previous_rating = self.get_average_rating()
        ratings = []
        for review in due_reviews:
            rating = int(review["rating"])
            self._apply_review_rating(rating)
            ratings.append(rating)
            self.state.review_history.append(dict(review))

        self.state.review_history = self.state.review_history[-100:]

        self.state.pending_reviews = [
            review for review in self.state.pending_reviews
            if review.get("created_day", self.state.day) >= self.state.day
        ]
        current_rating = self.get_average_rating()
        if (
            current_rating is not None
            and (
                self.state.historical_highest_rating is None
                or current_rating > self.state.historical_highest_rating
            )
        ):
            self.state.historical_highest_rating = current_rating
        result["events"].append(f"晨间更新了{len(ratings)}条评价。")
        if current_rating is not None and current_rating != previous_rating:
            if previous_rating is None:
                result["events"].append(f"营地评分更新为 {current_rating:.1f}★。")
            else:
                direction = "升至" if current_rating > previous_rating else "降至"
                result["events"].append(
                    f"营地评分由 {previous_rating:.1f}★ {direction} {current_rating:.1f}★。"
                )

    def _apply_review_rating(self, rating: int):
        self.state.total_reviews += 1
        self.state.total_rating_sum += rating

    def _get_review_score(self, npc: NPCGroup) -> float:
        """评价只额外读取已记录的负向体验，不改写客组最终满意度。"""
        return npc.total_satisfaction - npc.negative_experience_total

    def _calculate_rating(self, review_score: float) -> int:
        if review_score >= 76:
            return 5
        elif review_score >= 64:
            return 4
        elif review_score >= 56:
            return 3
        elif review_score >= 48:
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

    def _find_available_or_broken_tent(self, group_size: int) -> Optional[int]:
        """优先正常帐篷，无则选无人占用且容量合适的 broken 帐篷。"""
        for tent in self._get_available_unlocked_tents(group_size):
            return tent.id
        for tent in self._get_unlocked_tents():
            if tent.status == "broken" and tent.occupied_by is None and tent.capacity >= group_size:
                return tent.id
        return None

    def _match_day_to_overnight_tents(
        self,
        candidate_guests: list[NPCGroup],
        available_tents: list[Tent],
    ) -> dict[int, int]:
        """为日转夜候选客组整体匹配帐篷，不修改任何输入或游戏状态。"""
        candidates = list(candidate_guests)
        tents = list(available_tents)
        best_score = None
        best_matches = []

        def search(
            tent_index: int,
            used_guest_indexes: set[int],
            assignments: dict[int, int],
            capacity_waste: int,
            individual_wastes: list[int],
            used_broken_count: int,
        ) -> None:
            nonlocal best_score, best_matches
            if tent_index == len(tents):
                waste_distribution_score = tuple(
                    -waste for waste in sorted(individual_wastes, reverse=True)
                )
                score = (
                    len(assignments),
                    -used_broken_count,
                    -capacity_waste,
                    waste_distribution_score,
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best_matches = [assignments]
                elif score == best_score:
                    best_matches.append(assignments)
                return

            tent = tents[tent_index]
            search(
                tent_index + 1,
                used_guest_indexes,
                assignments,
                capacity_waste,
                individual_wastes,
                used_broken_count,
            )
            for guest_index, guest in enumerate(candidates):
                if (
                    guest_index in used_guest_indexes
                    or tent.capacity < guest.group_size
                ):
                    continue
                search(
                    tent_index + 1,
                    used_guest_indexes | {guest_index},
                    {**assignments, guest.id: tent.id},
                    capacity_waste + tent.capacity - guest.group_size,
                    individual_wastes + [tent.capacity - guest.group_size],
                    used_broken_count + (1 if tent.status == "broken" else 0),
                )

        search(0, set(), {}, 0, [], 0)
        if not best_matches:
            return {}
        if len(best_matches) == 1:
            return best_matches[0]
        return random.choice(best_matches)

    def _has_available_capacity(self) -> bool:
        return any(t.status == "available" for t in self._get_unlocked_tents())

    def _record_current_turn_dining_shortage_preview(self) -> None:
        """进入经营 Turn 后按正式顺序预判食材不足，仅记录一次提示。"""
        simulated_stock = self.state.food_stock
        shortage_ids = []
        for entry in self.state.today_arrival_plan:
            if entry.get("planned_day") != self.state.day:
                continue
            npc = self._find_npc(entry.get("npc_id"))
            if npc is None or npc.has_left or entry.get("arrival_status") != "arrived":
                continue
            for action in entry.get("planned_actions", []):
                if (
                    action.get("action") != "dining"
                    or action.get("planned_turn") != self.state.turn
                    or action.get("status") != "pending"
                    or self._has_consumed_dining_today(npc)
                ):
                    continue
                if simulated_stock < npc.group_size:
                    shortage_ids.append(npc.id)
                else:
                    simulated_stock -= npc.group_size
        if not shortage_ids:
            return
        for event in self.state.event_history:
            if (
                event.get("day") == self.state.day
                and event.get("turn") == self.state.turn
                and event.get("event_type") == "dining_shortage"
                and set(event.get("guest_ids", [])) == set(shortage_ids)
            ):
                return
        self._record_business_event(
            self.state.day, self.state.turn, "dining_shortage",
            guest_ids=shortage_ids, merge=False,
        )

    def _find_npc(self, npc_id: int) -> Optional[NPCGroup]:
        for npc in self.npc_pool:
            if npc.id == npc_id:
                return npc
        return None

    def _find_occupied_tent_for_npc(self, npc_id: int) -> Optional[Tent]:
        for tent in self.tents.values():
            if tent.occupied_by == npc_id:
                return tent
        return None

    def _get_active_overnight_tent_npcs(self) -> list[NPCGroup]:
        return [
            npc for npc in self.npc_pool
            if npc.visit_type == "overnight"
            and not npc.has_left
            and self._find_occupied_tent_for_npc(npc.id) is not None
        ]

    def _get_planned_checkout_turn(self, npc_id: int) -> Optional[int]:
        """从当天到达计划 entry 读取已确定的退房 Turn（仅合法值 1/2）。"""
        for entry in self.state.today_arrival_plan:
            if entry.get("npc_id") == npc_id:
                value = entry.get("checkout_turn")
                if value in (1, 2):
                    return value
                return None
        return None

    def _ensure_checkout_turn(self, npc: NPCGroup) -> Optional[int]:
        if (
            npc.visit_type != "overnight"
            or npc.has_left
            or self._find_occupied_tent_for_npc(npc.id) is None
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
            self._find_occupied_tent_for_npc(npc.id).id
            for npc in self._get_active_overnight_tent_npcs()
            if npc.checkout_turn == 2
        }
        return sorted(tent_ids)

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
            if (
                entry.get("source") == "reservation"
                and entry.get("visit_type") == "overnight"
            ):
                tent_id = entry.get("tent_id")
                if tent_id is None or tent_id not in self.tents:
                    continue
                tent = self.tents[tent_id]
                if not self._is_tent_unlocked(tent):
                    continue

                existing_reserved_npc = None
                for npc in self.npc_pool:
                    if (
                        npc.id == entry["npc_id"]
                        and npc.is_reserved
                        and not npc.has_left
                        and npc.location == f"tent_{tent_id}"
                    ):
                        existing_reserved_npc = npc
                        break

                if existing_reserved_npc is not None:
                    entry["arrival_status"] = "arrived"
                    continue

                if tent.status not in ["available", "reserved", "broken"]:
                    continue
                if tent.status == "broken" and tent.occupied_by is not None:
                    continue

                guest = NPCGroup(
                    id=entry["npc_id"],
                    group_size=entry["group_size"],
                    visit_type="overnight",
                    total_satisfaction=entry["total_satisfaction"],
                    is_reserved=True,
                    paid=entry.get("paid", False),
                )
                guest.economic_level = entry["economic_level"]
                guest.spending_habit = entry["spending_habit"]
                guest.temperament = entry["temperament"]
                self._checkin_npc(guest, tent_id, result, charge=False)
                entry["arrival_status"] = "arrived"
                continue

            if entry.get("visit_type") == "day":
                if entry.get("source") != "reservation" and self.get_day_campsite_remaining() <= 0:
                    entry["arrival_status"] = "turned_away_full"
                    result["events"].append(
                        "日间营位已经客满，一组刚到的客人只能遗憾离开。"
                        f"{self._get_temperament_service_reaction(NPCGroup(id=entry['npc_id'], group_size=entry['group_size'], visit_type='day', temperament=entry['temperament']), 'campsite_full')}"
                    )
                    continue

                guest = NPCGroup(
                    id=entry["npc_id"],
                    group_size=entry["group_size"],
                    visit_type="day",
                    total_satisfaction=entry["total_satisfaction"],
                    is_reserved=entry.get("is_reserved", False),
                    paid=entry.get("paid", False),
                )
                guest.economic_level = entry["economic_level"]
                guest.spending_habit = entry["spending_habit"]
                guest.temperament = entry["temperament"]
                if self._assign_campsite_slot(guest) is None:
                    entry["arrival_status"] = "turned_away_full"
                    result["events"].append(
                        "日间营位已经客满，一组刚到的客人只能遗憾离开。"
                        f"{self._get_temperament_service_reaction(guest, 'campsite_full')}"
                    )
                    continue
                guest.location = "campsite"
                guest.arrival_turn = self.state.turn
                self._apply_greenery_entry_bonus_once(guest)
                self.npc_pool.append(guest)
                self._record_served_group_once(guest)
                self.state.day_campsite_groups_served += 1
                if not entry.get("paid", False):
                    self.state.balance += self.CAMPSITE_FEE
                    self.state.today_income["campsite"] += self.CAMPSITE_FEE
                entry["arrival_status"] = "arrived"
                if entry.get("source") == "reservation":
                    result["events"].append(f"一组{guest.group_size}人的日间预约客到达。")
                else:
                    result["events"].append(
                        f"一组{guest.group_size}人日间游客到达（营位费+{self.CAMPSITE_FEE}）"
                    )
                continue

            if entry.get("source") == "natural_overnight":
                guest = NPCGroup(
                    id=entry["npc_id"],
                    group_size=entry["group_size"],
                    visit_type="overnight",
                    total_satisfaction=entry["total_satisfaction"],
                )
                guest.economic_level = entry["economic_level"]
                guest.spending_habit = entry["spending_habit"]
                guest.temperament = entry["temperament"]
                tent_id = self._find_available_or_broken_tent(guest.group_size)
                if tent_id is not None:
                    self._checkin_npc(guest, tent_id, result, charge=True)
                    entry["arrival_status"] = "arrived"
                else:
                    entry["arrival_status"] = "turned_away_full"
                    has_suitable_tent = any(
                        tent.capacity >= guest.group_size
                        for tent in self._get_unlocked_tents()
                    )
                    if has_suitable_tent:
                        result["events"].append(
                            "目前没有空余的合适帐篷，只能遗憾离开。"
                            f"{self._get_temperament_service_reaction(guest, 'tent_unavailable')}"
                        )
                    else:
                        result["events"].append(
                            "目前没有适合这组客人的帐篷，只能遗憾离开。"
                            f"{self._get_temperament_service_reaction(guest, 'tent_unavailable')}"
                        )

    def _settle_current_turn_arrivals(self) -> None:
        """在进入营业 Turn 后一次性落定当轮到达，供 actions 读取前使用。"""
        if self.state.turn not in (2, 3, 4):
            return
        arrival_result = {"events": []}
        self._process_planned_arrivals(arrival_result)
        arrived = [
            entry for entry in self.state.today_arrival_plan
            if entry.get("planned_day") == self.state.day
            and entry.get("arrival_turn") == self.state.turn
            and entry.get("arrival_status") == "arrived"
            and not entry.get("arrival_log_recorded")
        ]
        if arrived:
            self._record_business_event(
                self.state.day, self.state.turn, "arrival",
                guest_ids=[entry["npc_id"] for entry in arrived],
            )
            for entry in arrived:
                entry["arrival_log_recorded"] = True

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

    def _choose_weighted_unlocked_tier_key(
        self,
        facility_level: int,
        economic_level: int,
        tier_order: tuple[str, ...],
        tier_weight_table: dict[int, dict[str, int]],
    ) -> str:
        max_index = min(max(facility_level, 0), len(tier_order) - 1)
        unlocked_tier_keys = list(tier_order[: max_index + 1])
        weight_map = tier_weight_table.get(economic_level, tier_weight_table[1])
        unlocked_weights = [weight_map.get(tier_key, 0) for tier_key in unlocked_tier_keys]
        if not unlocked_tier_keys or not any(weight > 0 for weight in unlocked_weights):
            raise ValueError("no selectable unlocked tiers")
        return random.choices(unlocked_tier_keys, weights=unlocked_weights, k=1)[0]

    def _set_next_breakdown(self, tent: Tent):
        if not self._is_tent_unlocked(tent):
            tent.next_breakdown_turn = 0
            return
        base_interval = 15
        interval = random.randint(base_interval, base_interval + 10)
        tent.next_breakdown_turn = self._absolute_turn() + interval

    def _settle_startup_debt_on_day_26(self, result: Optional[dict]) -> None:
        """在 Day 26 晨间执行一次启动资金结算。"""
        if (
            self.state.day != self.STARTUP_DEBT_SETTLEMENT_DAY
            or self.state.startup_debt_settlement_completed
        ):
            return

        balance_before = self.state.balance
        debt_before = self.state.debt_remaining
        amount = min(balance_before, debt_before)
        self.state.balance -= amount
        self.state.debt_remaining -= amount
        self.state.startup_debt_settlement_completed = True

        if self.state.debt_remaining == 0:
            self._unlock_achievement("debt_paid_by_deadline")
            message = "Day 26 晨间已自动结清全部启动资金。"
        else:
            self._unlock_achievement("debt_unpaid_by_deadline")
            message = (
                "本次未能全部结清启动资金，存档仍可继续经营；"
                "如愿意，也可以自行重新开始新存档。"
            )
        if result is not None:
            result["events"].append(message)
        self._record_business_event(
            self.state.day,
            self.state.turn,
            "startup_debt_settlement",
            data={
                "amount": amount,
                "balance_before": balance_before,
                "balance_after": self.state.balance,
                "debt_before": debt_before,
                "debt_after": self.state.debt_remaining,
            },
            kind="system",
            merge=False,
        )

    def _new_day(self, result: Optional[dict] = None):
        """新的一天。修复 #5：绿化衰减逻辑"""
        income_total = sum(self.state.today_income.values())
        debt_repayment_total = sum(
            int(event.get("data", {}).get("amount", 0) or 0)
            for event in self.state.event_history
            if event.get("day") == self.state.day
            and event.get("event_type") == "repay_debt"
            and event.get("action") == "repay_debt"
        )
        expense_total = (
            self.state.day_start_balance
            + income_total
            - self.state.balance
            - debt_repayment_total
        )
        self.state.previous_day_summary = {
            "day": self.state.day,
            "income_total": income_total,
            "expense_total": expense_total,
            "net_income": income_total - expense_total,
            "guest_groups_served": sum(
                1
                for entry in self.state.today_arrival_plan
                if entry.get("planned_day") == self.state.day
                and entry.get("arrival_status") == "arrived"
            ),
        }

        if self.state.day == 1:
            self._unlock_achievement("first_day_complete")
        review_result = {"events": []}
        for npc in self._get_active_overnight_tent_npcs():
            self._try_leave_review(npc, review_result)

        # 修复 #5：先根据上一日是否处理绿化决定是否衰减
        if not self.state.greenery_processed_today:
            # 上一日没有处理绿化，自动衰减一次
            decay_before, decay_after = self._process_greenery_decay()
            if (
                result is not None
                and decay_before is not None
                and decay_after is not None
                and decay_after < decay_before
            ):
                result["events"].append(
                    f"昨日未维护绿化，绿化值 {decay_before:.1f} → {decay_after:.1f}。"
                )

        self.state.day += 1
        self.state.turn = 1
        self.state.today_income = {
            "accommodation": 0,
            "campsite": 0,
            "dining": 0,
            "entertainment": 0,
            "hot_spring": 0,
            "nature_observation": 0,
            "tip": 0,
        }
        self.state.today_expenses = {
            "food": 0,
            "greenery": 0,
            "repair": 0,
            "conflict_care": 0,
            "growth": 0,
            "lodging_consumables": 0,
            "hot_spring_operating": 0,
        }
        self.state.today_events = []
        self.state.decisions_left = self.DAILY_DECISION_LIMIT
        self.state.improve_service_uses_today = 0
        self.state.clean_campsite_uses_today = 0
        self.state.pending_turn_plan = None
        self.state.today_conflict_event = None
        self.state.post_used_today = False
        self.state.pending_post_reservation = None
        self.state.campfire_affected_npc_ids = []
        self.state.stargazing_affected_npc_ids = []
        self.state.today_tip_settled = False
        self.state.day_campsite_groups_served = 0
        self.state.hot_spring_people_served_today = 0
        # 重置绿化标记
        self.state.greenery_processed_today = False
        if self.state.day == 20 and result is not None:
            result["events"].append("提醒：Day 26 晨间将统一结算启动资金。")
        if self.state.day == 25 and result is not None:
            result["events"].append("提醒：明早将结算启动资金。")

        self._settle_pending_reviews(result if result is not None else {"events": []})
        self._update_campsite_star()

        self._ensure_today_arrival_plan()
        self._assign_reserved_tents_for_today()
        self._generate_daily_reservation()
        self._settle_startup_debt_on_day_26(result)
        self.state.day_start_balance = self.state.balance

    def _process_greenery_decay(self) -> tuple[Optional[float], Optional[float]]:
        """绿化衰减"""
        facility = self.facilities["greenery"]
        if facility.level >= 2:
            return None, None

        before = round(facility.greenery_satisfaction, 1)
        facility.greenery_satisfaction = round(
            max(0.0, facility.greenery_satisfaction - 0.5), 1
        )
        return before, facility.greenery_satisfaction

    def _generate_daily_reservation(self):
        profile = self._ensure_daily_demand_profile()
        if profile.get("reservations_processed"):
            return

        target_day = self.state.day + 1
        day_groups = 0
        overnight_groups = 0
        campsite_reservation_income = 0
        overnight_reservation_income = 0
        for _ in range(self.DAY_CAMPSITE_CAPACITY):
            if random.random() >= 0.15:
                continue
            group_size = random.randint(1, 6)
            reservation = self._create_reservation_record(
                group_size=group_size,
                visit_type="day",
                arrival_day=target_day,
                paid=True,
                status="accepted",
            )
            self.state.reservations.append(reservation)
            self.state.balance += self.CAMPSITE_FEE
            self.state.today_income["campsite"] += self.CAMPSITE_FEE
            campsite_reservation_income += self.CAMPSITE_FEE
            day_groups += 1

        for _ in self._get_unlocked_tents():
            if random.random() >= 0.15:
                continue
            group_size = random.randint(1, 6)
            tent = self._find_reservable_overnight_tent(group_size)
            if tent is None:
                continue
            reservation = self._create_reservation_record(
                group_size=group_size,
                visit_type="overnight",
                arrival_day=target_day,
                paid=True,
                status="accepted",
            )
            reservation["tent_id"] = tent.id
            self.state.reservations.append(reservation)
            payment = self.TENT_PRICES[tent.id]
            self.state.balance += payment
            self.state.today_income["accommodation"] += payment
            overnight_reservation_income += payment
            overnight_groups += 1

        if day_groups and overnight_groups:
            self.state.today_events.append(
                f"接到明日{day_groups}组日间营位预约、{overnight_groups}组帐篷预约，已收取营位费{campsite_reservation_income}金币、住宿费{overnight_reservation_income}金币。"
            )
        elif day_groups:
            self.state.today_events.append(
                f"接到明日{day_groups}组日间营位预约，已收取营位费{campsite_reservation_income}金币。"
            )
        elif overnight_groups:
            self.state.today_events.append(
                f"接到明日{overnight_groups}组帐篷预约，已收取住宿费{overnight_reservation_income}金币。"
            )

        profile["reservations_processed"] = True

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
                    "campsite_slot": n.campsite_slot,
                    "has_left": n.has_left,
                    "review_left": n.review_left,
                    "review_rating": n.review_rating,
                    "is_reserved": n.is_reserved,
                    "paid": n.paid
                })

        safe_reservations = [
            {
                "group_size": reservation["group_size"],
                "visit_type": reservation["visit_type"],
                "arrival_day": reservation["arrival_day"],
                "status": reservation["status"],
                **(
                    {"tent_id": reservation["tent_id"]}
                    if reservation.get("visit_type") == "overnight"
                    else {}
                ),
            }
            for reservation in self.state.reservations
        ]

        # 修复：对外只暴露帐篷必要字段，隐藏 next_breakdown_turn
        safe_tents = {
            tid: {
                "id": t.id,
                "capacity": t.capacity,
                "unlocked": t.is_unlocked,

                "status": t.status,
                "needs_cleaning": t.needs_cleaning,
                "occupied_by": t.occupied_by
            }
            for tid, t in self.tents.items()
        }

        greenery = self.facilities["greenery"]
        greenery_max = round(self.GREENERY_LEVEL_MAX.get(greenery.level, 10.0), 1)
        greenery_value = round(greenery.greenery_satisfaction, 1)
        greenery_maintained_today = self.state.greenery_processed_today
        greenery_decay_next_day = 0.0
        if greenery.level < 2 and not greenery_maintained_today:
            greenery_decay_next_day = 0.5

        return {
            "day": self.state.day,
            "turn": self.state.turn,
            "balance": self.state.balance,
            "average_rating": self.get_average_rating(),
            "campsite_star": self.get_campsite_star_progress(),
            "tents": safe_tents,
            "facilities": {k: asdict(v) for k, v in self.facilities.items()},
            "greenery": {
                "level": greenery.level,
                "value": greenery_value,
                "max": greenery_max,
                "maintained_today": greenery_maintained_today,
                "decay_next_day": greenery_decay_next_day,
            },
            "nature_observation": {
                "station_built": self.state.nature_observation_station_built,
                "discovered_count": len(self.state.discovered_insects),
                "total_count": len(self.INSECT_CATALOG),
                "discovered_insects": [
                    {
                        "id": insect["id"],
                        "name": insect["name"],
                        "rarity": insect["rarity"],
                        "catalog_index": index,
                    }
                    for index, insect in enumerate(self.INSECT_CATALOG, start=1)
                    if insect["id"] in self.state.discovered_insects
                ],
            },
            "active_npcs": safe_npcs,
            "reservations": safe_reservations,
            "decisions_left": self.state.decisions_left,
            "food_stock": self.state.food_stock,
            "today_income": self.state.today_income,
            "today_expenses": self.state.today_expenses,
            "achievements": self.get_achievement_state(),
        }

    def _get_tents_summary(self) -> dict:
        return {tid: {
            "status": t.status,
            "needs_cleaning": t.needs_cleaning,
            "unlocked": t.is_unlocked,
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
        greenery = state["greenery"]
        lines = [
            f"📍 第{state['day']}天 · 回合{state['turn']}",
            f"💰 余额: {state['balance']}金币",
            (
                f"⭐ 营地评价: {state['average_rating']:.1f} ★"
                if state["average_rating"] is not None
                else "⭐ 营地评价: -- ★"
            ),
            (
                "今日经营决策点："
                f"{state['decisions_left']} / {self.DAILY_DECISION_LIMIT}"
            ),
            "",
            "--- 帐篷状态 ---"
        ]
        for tid, tent in state["tents"].items():
            si = {"available": "🟢", "occupied": "🔴",
                  "cleaning": "🟡", "broken": "⚠️", "reserved": "🔵"}.get(tent["status"], "❓")
            line = f"  {tid}号: {si} 容量{tent['capacity']}人"
            if tent["occupied_by"]:
                line += " (有客人)"
            lines.append(line)

        lines.extend([
            "",
            "--- 设施 ---",
            f"  餐饮区 Lv.{state['facilities']['dining']['level']}",
            f"  娱乐区 Lv.{state['facilities']['entertainment']['level']}",
            f"  绿化值 Lv.{greenery['level']}：{greenery['value']:g} / {greenery['max']:g}",
            (
                "  绿化状态稳定，不会自然衰减"
                if greenery["level"] >= 2
                else (
                    "  今日已维护，次日不会衰减"
                    if greenery["maintained_today"]
                    else f"  今日未维护，次日将下降 {greenery['decay_next_day']:.1f}"
                )
            ),
            "",
            "--- 今日收入 ---",
            f"  住宿: +{state['today_income']['accommodation']}",
            f"  营位: +{state['today_income']['campsite']}",
            f"  餐饮: +{state['today_income']['dining']}",
            f"  娱乐: +{state['today_income']['entertainment']}"
        ])
        return "\n".join(lines)
