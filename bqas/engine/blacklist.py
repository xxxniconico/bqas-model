"""BQAS 一票否决清单

9 项检查，按成本从低到高排序。
全部通过才能进入评分环节。
"""

import logging
from datetime import date, datetime
from ..data.schema import FinancialData

logger = logging.getLogger(__name__)

PASS = (True, "", {})
FAIL = lambda reason, checks: (False, reason, checks)

def _fail(reason, checks):
    """Helper: return FAIL as 3 separate values"""
    return False, reason, checks


def check_blacklist(data: FinancialData) -> tuple[bool, str, dict]:
    """执行全部 9 项一票否决检查

    Returns:
        (passed, fail_reason, check_details)
        check_details = {"check_name": (passed, detail)}
    """
    checks = {}
    info = data.info
    income = data.income
    balance = data.balance
    cashflow = data.cashflow
    quotes = data.quotes

    # ── 1. ST / *ST（成本最低）──
    if info.is_st:
        checks["st_flag"] = (False, "ST/*ST 股票")
        return FAIL("ST/*ST 股票", checks)
    checks["st_flag"] = (True, "")

    # ── 2. 上市不满 3 年 ──
    if info.listing_date:
        days_listed = (date.today() - info.listing_date).days
        if days_listed < 365 * 3:
            checks["listing_days"] = (False, f"上市仅 {days_listed} 天，不足 3 年")
            return FAIL(f"上市不满 3 年 ({days_listed} 天)", checks)
    checks["listing_days"] = (True, "")

    # ── 3. 市值 < 30 亿 ──
    if quotes:
        latest_mcap = quotes[-1].market_cap
        if 0 < latest_mcap < 3_000_000_000:  # 30 亿 = 3e9 元
            checks["market_cap"] = (False, f"市值 {latest_mcap/1e8:.1f} 亿 < 30 亿")
            return FAIL(f"市值 {latest_mcap/1e8:.1f} 亿 < 30 亿", checks)
    checks["market_cap"] = (True, "")

    # ── 4. 日均成交 < 2000 万 ──
    if quotes:
        recent_quotes = quotes[-20:]  # 近 20 日
        if recent_quotes:
            avg_turnover = sum(q.turnover_amount for q in recent_quotes) / len(recent_quotes)
            if avg_turnover < 20_000_000:  # 2000 万
                checks["avg_volume"] = (False, f"日均成交 {avg_turnover/1e4:.0f} 万 < 2000 万")
                return FAIL(f"日均成交 {avg_turnover/1e4:.0f} 万 < 2000 万", checks)
    checks["avg_volume"] = (True, "")

    # ── 5. 连续 3 年净利润 < 0 ──
    recent_income = sorted(income, key=lambda x: x.report_year, reverse=True)[:3]
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
    if data.audit_opinion and data.audit_opinion != "standard":
        checks["audit_opinion"] = (False, f"审计意见: {data.audit_opinion}")
        return FAIL(f"非标审计意见: {data.audit_opinion}", checks)
    checks["audit_opinion"] = (True, "")

    # ── 8. 商誉炸弹 ──
    if balance:
        latest_balance = sorted(balance, key=lambda x: x.report_year, reverse=True)[0]
        if latest_balance.equity > 0:
            goodwill_ratio = latest_balance.goodwill / latest_balance.equity
            if goodwill_ratio > 0.5 and latest_balance.goodwill > 1_000_000_000:  # >10 亿
                checks["goodwill_bomb"] = (False, f"商誉/净资产={goodwill_ratio:.1%} > 50%")
                return FAIL(f"商誉炸弹: 商誉/净资产={goodwill_ratio:.1%}", checks)
    checks["goodwill_bomb"] = (True, "")

    # ── 9. 大股东高质押 ──
    if data.pledge_ratio > 0.70:
        checks["pledge_ratio"] = (False, f"质押比例 {data.pledge_ratio:.0%} > 70%")
        return FAIL(f"大股东质押 {data.pledge_ratio:.0%} > 70%", checks)
    checks["pledge_ratio"] = (True, "")

    # ── 10. 财务造假处罚（5年内）──
    if data.has_financial_fraud_penalty:
        checks["financial_fraud"] = (False, "5年内有财务造假处罚记录")
        return FAIL("5年内有财务造假处罚记录", checks)
    checks["financial_fraud"] = (True, "")

    return True, "", checks
