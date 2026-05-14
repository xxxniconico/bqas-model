"""BQAS 一票否决清单

11 项检查，按成本从低到高排序，按市场差异化执行。
全部通过才能进入评分环节。

市场差异化：
  - cn (A股): 全部 11 项
  - hk (港股): 跳过 ST/质押/造假处罚/重述（无数据源），市值/HK$/商誉阈值调整
  - us (美股): 跳过 ST/质押/造假处罚/重述（无数据源），市值/USD/商誉阈值调整

阈值：
  - 市值: cn=¥30亿, hk=HK$35亿, us=$4B
  - 商誉: cn=¥10亿, hk=HK$12亿, us=$1.5B
  - 日均成交: cn=¥2000万, hk/us=跳过（无实时行情）

Beneish M-Score：全球通用（IFRS/US GAAP），threshold=-1.78
"""

import logging
from datetime import date, datetime
from ..data.schema import FinancialData

logger = logging.getLogger(__name__)

PASS = (True, "", {})
FAIL = lambda reason, checks: (False, reason, checks)

# ── Market-specific thresholds ──
MARKET_THRESHOLDS = {
    "cn": {"market_cap": 30_000_000_000,  "goodwill": 10_000_000_000, "volume": 20_000_000, "currency": "¥"},
    "hk": {"market_cap": 35_000_000_000,  "goodwill": 12_000_000_000, "volume": None,         "currency": "HK$"},
    "us": {"market_cap":  4_000_000_000,  "goodwill":  1_500_000_000, "volume": None,         "currency": "$"},
}


def check_blacklist(data: FinancialData, market: str = "cn") -> tuple[bool, str, dict]:
    """执行一票否决检查，按市场适配。

    Args:
        data: FinancialData object with all financial data
        market: 'cn' (A股), 'hk' (港股), or 'us' (美股)

    Returns:
        (passed, fail_reason, check_details)
    """
    checks = {}
    th = MARKET_THRESHOLDS.get(market, MARKET_THRESHOLDS["cn"])
    is_cn = (market == "cn")
    is_global = not is_cn

    info = data.info
    income = data.income
    balance = data.balance
    cashflow = data.cashflow
    quotes = data.quotes

    # ═══ A股专属检查 ═══

    # ── 1. ST / *ST（仅 A 股）──
    if is_cn and info.is_st:
        checks["st_flag"] = (False, "ST/*ST 股票")
        return FAIL("ST/*ST 股票", checks)
    checks["st_flag"] = (True, "" if is_cn else "N/A（非A股）")

    # ═══ 通用检查（市场无关） ═══

    # ── 2. 上市不满 3 年 ──
    if info.listing_date:
        days_listed = (date.today() - info.listing_date).days
        if days_listed < 365 * 3:
            checks["listing_days"] = (False, f"上市仅 {days_listed} 天，不足 3 年")
            return FAIL(f"上市不满 3 年 ({days_listed} 天)", checks)
    checks["listing_days"] = (True, "")

    # ── 3. 市值过低（按市场设阈值）──
    if quotes:
        latest_mcap = quotes[-1].market_cap
        if 0 < latest_mcap < th["market_cap"]:
            cap_str = f"{latest_mcap/1e8:.1f} 亿"
            th_str = f"{th['market_cap']/1e8:.0f} 亿"
            checks["market_cap"] = (False, f"市值 {cap_str} < {th_str}")
            return FAIL(f"市值 {cap_str} < {th_str}", checks)
    checks["market_cap"] = (True, "")

    # ── 4. 日均成交过低（仅 A 股有实时行情）──
    if is_cn and quotes and th["volume"] is not None:
        recent_quotes = quotes[-20:]
        if recent_quotes:
            avg_turnover = sum(q.turnover_amount for q in recent_quotes) / len(recent_quotes)
            if avg_turnover < th["volume"]:
                checks["avg_volume"] = (False, f"日均成交 {avg_turnover/1e4:.0f} 万 < {th['volume']/1e4:.0f} 万")
                return FAIL(f"日均成交 {avg_turnover/1e4:.0f} 万 < {th['volume']/1e4:.0f} 万", checks)
        checks["avg_volume"] = (True, "")
    else:
        checks["avg_volume"] = (True, "N/A（无实时行情）" if is_global else "")

    # ── 5. 连续 3 年净利润 < 0 ──
    valid_income = [i for i in income if i.revenue > 0]  # skip empty years
    recent_income = sorted(valid_income, key=lambda x: x.report_year, reverse=True)[:3]
    if len(recent_income) >= 3 and all(i.net_income < 0 for i in recent_income):
        checks["net_loss_3y"] = (False, "连续 3 年净利润为负")
        return FAIL("连续 3 年净利润为负", checks)
    checks["net_loss_3y"] = (True, "")

    # ── 6. 连续 3 年 OCF < 0 ──
    recent_cf = sorted(cashflow, key=lambda x: x.report_year, reverse=True)[:3]
    if len(recent_cf) >= 3 and all(c.operating_cf < 0 for c in recent_cf):
        checks["ocf_negative_3y"] = (False, "连续 3 年经营现金流为负")
        return FAIL("连续 3 年经营现金流为负", checks)
    checks["ocf_negative_3y"] = (True, "")

    # ── 7. 非标审计意见 ──
    if not data.audit_opinion:
        checks["audit_opinion"] = (True, "审计数据缺失，跳过检查")
    elif data.audit_opinion.replace('\u3000', '').strip() not in (
        "standard", "标准无保留意见", "标准无保留",
        "Unqualified", "Unqualified Opinion",  # US/HK English variants
    ):
        checks["audit_opinion"] = (False, f"审计意见: {data.audit_opinion}")
        return FAIL(f"非标审计意见: {data.audit_opinion}", checks)
    else:
        checks["audit_opinion"] = (True, "标准无保留")

    # ── 8. 商誉炸弹（按市场设阈值）──
    if balance:
        valid_balance = [b for b in balance if b.total_assets > 0]
        if valid_balance:
            latest_balance = sorted(valid_balance, key=lambda x: x.report_year, reverse=True)[0]
            if latest_balance.equity > 0:
                goodwill_ratio = latest_balance.goodwill / latest_balance.equity
                if goodwill_ratio > 0.5 and latest_balance.goodwill > th["goodwill"]:
                    checks["goodwill_bomb"] = (False, f"商誉/净资产={goodwill_ratio:.1%} > 50%")
                    return FAIL(f"商誉炸弹: 商誉/净资产={goodwill_ratio:.1%}", checks)
    checks["goodwill_bomb"] = (True, "")

    # ═══ A股专属检查（无数据源可跳过）═══

    # ── 9. 大股东高质押（仅 A 股，且数据常为 0）──
    if is_cn and data.pledge_ratio > 0.70:
        checks["pledge_ratio"] = (False, f"质押比例 {data.pledge_ratio:.0%} > 70%")
        return FAIL(f"大股东质押 {data.pledge_ratio:.0%} > 70%", checks)
    checks["pledge_ratio"] = (True, "" if is_cn else "N/A（非A股）")

    # ── 10. 财务造假处罚 5 年内（仅 A 股有 CSRC 数据）──
    if is_cn and data.has_financial_fraud_penalty:
        checks["financial_fraud"] = (False, "5年内有财务造假处罚记录")
        return FAIL("5年内有财务造假处罚记录", checks)
    checks["financial_fraud"] = (True, "" if is_cn else "N/A（非A股）")

    # ── 11. 会计差错更正 / 财务重述（仅 A 股有东方财富公告监控）──
    if is_cn and data.restatement_risk:
        checks["restatement"] = (False, f"会计重述: {data.restatement_detail}")
        return FAIL(f"会计差错更正: {data.restatement_detail}", checks)
    checks["restatement"] = (True, "" if is_cn else "N/A（非A股）")

    return True, "", checks
