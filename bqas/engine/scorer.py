"""BQAS 评分引擎 — 主入口

组装一票否决 + 因子计算 → BQAS 总分 + 评级。
"""

import logging
from ..data.fetcher import fetch_financials, fetch_quotes
from ..data.schema import FinancialData
from .blacklist import check_blacklist
from .industry import get_industry_group
from .factors import compute_all_factors

logger = logging.getLogger(__name__)

# 评级体系
RATING_TABLE = [
    (85, "⭐⭐⭐⭐⭐", "巴菲特级别", "重仓候选"),
    (75, "⭐⭐⭐⭐",   "优秀企业",   "可配置"),
    (65, "⭐⭐⭐",    "良好企业",   "观察列表"),
    (50, "⭐⭐",     "一般",       "等更好价格"),
    (0,  "⭐",      "不合格",     "回避"),
]


def get_rating(total: float) -> dict:
    """分值 → 评级"""
    for threshold, stars, label, advice in RATING_TABLE:
        if total >= threshold:
            return {"stars": stars, "label": label, "advice": advice}
    return RATING_TABLE[-1][1:]


def score_stock(code: str, force_refresh: bool = False) -> dict:
    """单只股票完整评分

    Args:
        code: 6 位股票代码
        force_refresh: 是否强制刷新数据（忽略缓存）

    Returns:
        {
            "code": "600519",
            "name": "贵州茅台",
            "industry_group": "消费",
            "passed_blacklist": True,
            "blacklist_reason": "",
            "blacklist_checks": {...},
            "scores": {
                "quality": {"roe": {...}, ...},
                "value": {...},
                "health": {...},
                "gov": {...},
                "weighted": {"quality": N, "value": N, "health": N, "gov": N, "total": N},
            },
            "total": 72.2,
            "rating": "⭐⭐⭐",
            "rating_label": "良好企业",
            "rating_advice": "观察列表",
        }
    """
    # 1. 获取数据
    logger.info(f"Fetching data for {code}...")
    data = fetch_financials(code, years=5)
    quotes = fetch_quotes(code, start="2020-01-01")
    data.quotes = quotes

    # 2. 一票否决
    passed, reason, checks = check_blacklist(data)
    if not passed:
        return {
            "code": code,
            "name": data.info.name,
            "industry_group": get_industry_group(data.info.industry_sw),
            "passed_blacklist": False,
            "blacklist_reason": reason,
            "blacklist_checks": {k: v[1] for k, v in checks.items()},
            "total": 0,
            "rating": "⛔",
            "rating_label": "一票否决",
            "rating_advice": reason,
        }

    # 3. 因子计算
    industry_group = get_industry_group(data.info.industry_sw)
    logger.info(f"Computing factors for {code} [{industry_group}]...")
    factors = compute_all_factors(data, industry_group)

    total = factors["weighted"]["total"]
    rating = get_rating(total)

    return {
        "code": code,
        "name": data.info.name,
        "industry_sw": data.info.industry_sw,
        "industry_group": industry_group,
        "passed_blacklist": True,
        "blacklist_checks": {k: v[1] for k, v in checks.items()},
        "scores": factors,
        "total": total,
        "rating": rating["stars"],
        "rating_label": rating["label"],
        "rating_advice": rating["advice"],
    }
