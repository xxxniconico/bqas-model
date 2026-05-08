"""BQAS 行业映射 + 权重矩阵

申万一级 → BQAS 行业分组，各组维度权重。
"""

# 申万一级 → BQAS 分组
INDUSTRY_MAP = {
    "食品饮料": "消费", "白酒": "消费", "家用电器": "消费", "纺织服饰": "消费",
    "农林牧渔": "消费", "轻工制造": "消费", "商贸零售": "消费",
    "社会服务": "消费", "美容护理": "消费",
    "银行": "金融", "非银金融": "金融",
    "计算机": "科技", "电子": "科技", "通信": "科技",
    "传媒": "科技", "国防军工": "科技",
    "医药生物": "医药",
    "房地产": "地产", "建筑装饰": "地产", "建筑材料": "地产",
    "钢铁": "周期", "煤炭": "周期", "石油石化": "周期",
    "化工": "周期", "有色金属": "周期", "基础化工": "周期",
    "公用事业": "公用事业", "环保": "公用事业",
    "交通运输": "公用事业",
    "电力设备": "制造", "机械设备": "制造", "汽车": "制造",
    "综合": "其他",
}

# 行业权重矩阵
WEIGHT_MATRIX = {
    "消费":     {"quality": 0.40, "value": 0.25, "health": 0.20, "gov": 0.15},
    "金融":     {"quality": 0.25, "value": 0.35, "health": 0.25, "gov": 0.15},
    "科技":     {"quality": 0.35, "value": 0.30, "health": 0.15, "gov": 0.20},
    "医药":     {"quality": 0.40, "value": 0.25, "health": 0.20, "gov": 0.15},
    "地产":     {"quality": 0.25, "value": 0.35, "health": 0.25, "gov": 0.15},
    "周期":     {"quality": 0.30, "value": 0.30, "health": 0.25, "gov": 0.15},
    "公用事业": {"quality": 0.20, "value": 0.40, "health": 0.30, "gov": 0.10},
    "制造":     {"quality": 0.30, "value": 0.30, "health": 0.25, "gov": 0.15},
    "其他":     {"quality": 0.30, "value": 0.30, "health": 0.25, "gov": 0.15},
}

# 行业特殊规则
SPECIAL_RULES = {
    "金融": {
        "pb_weight_scale": 1.0,        # PB 权重维持
        "ev_weight_scale": 0.0,        # EV/OpE 权重归零，PB 替代
        "skip_leverage": True,          # 跳过负债率检查
    },
    "科技": {
        "pb_weight_scale": 0.3,        # PB 权重降至 30%
        "ev_weight_scale": 1.7,        # 差额加到 EV/OpE
    },
    "地产": {
        "use_nav_discount": True,       # 用 NAV 折价
    },
    "周期": {
        "use_10y_pb_percentile": True,  # 用 10 年 PB 分位数
    },
}


def get_industry_group(industry_sw: str) -> str:
    """申万一级行业 → BQAS 行业分组"""
    return INDUSTRY_MAP.get(industry_sw, "其他")


def get_weights(industry_group: str) -> dict:
    """获取行业四维度权重"""
    return WEIGHT_MATRIX.get(industry_group, WEIGHT_MATRIX["其他"])


def get_special_rules(industry_group: str) -> dict:
    """获取行业特殊规则"""
    return SPECIAL_RULES.get(industry_group, {})
