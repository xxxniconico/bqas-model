"""BQAS 因子计算引擎

4 大维度 × 10+ 子因子，基于设计文档 V2 最终版。
每个因子返回 0-10 分的标准化得分。
"""

import math
from statistics import mean, median, stdev
from ..data.schema import FinancialData, IncomeStatement, BalanceSheet, CashFlowStatement, DailyQuote
from .beneish import compute_beneish_m_score

# ── Industry PB median cache (computed from quotes table) ──
_industry_pb_cache: dict = {}

def _get_industry_pb_median(industry_group: str) -> float:
    """Get industry median PB from quotes table. Lazy-loads and caches."""
    global _industry_pb_cache
    if not _industry_pb_cache:
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).parent.parent / "data" / "cache" / "bqas.db"
        try:
            conn = sqlite3.connect(str(db_path))
            # Get latest PB for each stock in each industry
            rows = conn.execute("""
                SELECT si.industry_sw, q.pb
                FROM quotes q
                JOIN stock_info si ON q.code = si.code
                WHERE q.trade_date = (SELECT MAX(trade_date) FROM quotes)
                  AND q.pb > 0 AND q.pb < 100
                  AND si.industry_sw IS NOT NULL AND si.industry_sw != ''
            """).fetchall()
            conn.close()
            
            # Group by industry_group (mapped from industry_sw)
            from .industry import get_industry_group as _get_group
            by_group = {}
            for ind_sw, pb in rows:
                grp = _get_group(ind_sw)
                if grp not in by_group:
                    by_group[grp] = []
                by_group[grp].append(pb)
            
            from statistics import median as _median
            for grp, pbs in by_group.items():
                _industry_pb_cache[grp] = _median(pbs)
        except Exception:
            pass
    
    return _industry_pb_cache.get(industry_group, None)


# ═══════════════════════════════════════════════════════════
#  维度 I：企业质量 (Quality) — 35%
# ═══════════════════════════════════════════════════════════

def factor_roe_stability(income: list[IncomeStatement], balance: list[BalanceSheet], years: int = 5) -> dict:
    """Q1: ROE 稳定性（12%）

    ROE_5y = mean(net_income / equity)
    得分 = min(ROE_5y, 30%) / 30% × 10
    """
    if not income or not balance:
        return {"score": 0, "roe_5y": 0, "missing_data": True}

    bal_eq = {b.report_year: b.equity for b in balance}
    pairs = []
    for inc in income[-years:]:
        eq = bal_eq.get(inc.report_year, 0)
        if eq > 0 and inc.report_year in bal_eq:
            roe = inc.net_income / eq
            pairs.append(roe)

    if not pairs:
        return {"score": 0, "roe_5y": 0, "missing_data": True}

    roe_5y = mean(pairs)
    score = min(roe_5y, 0.30) / 0.30 * 10

    # DuPont decomposition (latest year with real data)
    dupont = {}
    if income and balance:
        real_income = [i for i in sorted(income, key=lambda x: x.report_year) if i.revenue > 0]
        real_balance = [b for b in sorted(balance, key=lambda x: x.report_year) if b.total_assets > 0]
        if real_income and real_balance:
            latest_inc = real_income[-1]
            latest_bal = real_balance[-1]
            if latest_inc.revenue > 0 and latest_bal.total_assets > 0 and latest_bal.equity > 0:
                net_margin = latest_inc.net_income / latest_inc.revenue
                asset_turnover = latest_inc.revenue / latest_bal.total_assets
                equity_mult = latest_bal.total_assets / latest_bal.equity
                dupont = {
                    "net_margin": round(net_margin, 4),
                    "asset_turnover": round(asset_turnover, 4),
                    "equity_multiplier": round(equity_mult, 2),
                    "roe_reconstructed": round(net_margin * asset_turnover * equity_mult, 4),
                }
    # ROE 趋势斜率（线性回归 β）— 区分"改善中"与"恶化中"
    if len(pairs) >= 3:
        n = len(pairs)
        x_mean = (n - 1) / 2
        y_mean = roe_5y
        num = sum((i - x_mean) * (pairs[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0  # ROE change per year
        # Trend adjustment: ±2 points on the ROE sub-score
        if slope > 0.03:       trend_adj = 2.0   # strongly improving (>3%/yr)
        elif slope > 0.01:     trend_adj = 1.0   # improving
        elif slope > -0.01:    trend_adj = 0     # stable
        elif slope > -0.03:    trend_adj = -1.0  # declining
        else:                  trend_adj = -2.0  # strongly declining
    else:
        slope, trend_adj = 0, 0

    # Base score + trend adjustment, capped 0-10
    score = max(0, min(10, score + trend_adj))

    return {"score": round(score, 1), "roe_5y": round(roe_5y, 4),
            "years_available": len(pairs), "dupont": dupont,
            "roe_trend": round(slope, 4), "trend_adj": trend_adj}


def factor_roic(income: list[IncomeStatement], balance: list[BalanceSheet], years: int = 5) -> dict:
    """Q2: ROIC 超额收益（10%）

    ROIC_5y = mean(EBIT / (equity + debt))
    得分 = min(ROIC_5y, 30%) / 30% × 10
    """
    if not income or not balance:
        return {"score": 0, "roic_5y": 0, "missing_data": True}

    pairs = []
    for inc in income[-years:]:
        bal = next((b for b in balance if b.report_year == inc.report_year), None)
        if bal:
            invested = bal.equity + bal.short_term_debt + bal.long_term_debt
            if invested > 0:
                pairs.append(inc.ebit / invested)

    if not pairs:
        return {"score": 0, "roic_5y": 0, "missing_data": True}

    roic_5y = mean(pairs)
    score = min(roic_5y, 0.30) / 0.30 * 10

    # ROIC 趋势斜率
    if len(pairs) >= 3:
        n = len(pairs)
        x_mean = (n - 1) / 2
        y_mean = roic_5y
        num = sum((i - x_mean) * (pairs[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0
        if slope > 0.03:       trend_adj = 2.0
        elif slope > 0.01:     trend_adj = 1.0
        elif slope > -0.01:    trend_adj = 0
        elif slope > -0.03:    trend_adj = -1.0
        else:                  trend_adj = -2.0
    else:
        slope, trend_adj = 0, 0

    score = max(0, min(10, score + trend_adj))
    return {"score": round(score, 1), "roic_5y": round(roic_5y, 4),
            "years_available": len(pairs), "roic_trend": round(slope, 4), "trend_adj": trend_adj}


def factor_cfo_quality(income: list[IncomeStatement], cashflow: list[CashFlowStatement], years: int = 3) -> dict:
    """Q3: CFO 真实性（8%）

    CFO_quality = mean(OCF) / mean(NI) — 近 3 年均值
    得分 = min(CFO_quality, 2.0) / 2.0 × 10
    """
    pairs = []
    for cf in cashflow[-years:]:
        inc = next((i for i in income if i.report_year == cf.report_year), None)
        if inc and inc.net_income != 0:
            pairs.append(cf.operating_cf / inc.net_income)

    if not pairs:
        # 降级：用全量
        for cf in cashflow:
            inc = next((i for i in income if i.report_year == cf.report_year), None)
            if inc and inc.net_income != 0:
                pairs.append(cf.operating_cf / inc.net_income)

    if not pairs:
        return {"score": 0, "cfo_quality": 0, "missing_data": True}

    cfo_quality = mean(pairs)
    score = min(cfo_quality, 2.0) / 2.0 * 10
    return {"score": round(score, 1), "cfo_quality": round(cfo_quality, 4)}


def factor_gross_margin_moat(income: list[IncomeStatement], years: int = 5) -> dict:
    """Q4: 毛利率护城河（5%）

    GM = (revenue - op_cost) / revenue  — 真实营业成本
    回退：若无 op_cost 数据，用 operating_profit / revenue 近似
    GM_median = median(gm[-3:])
    GM_stability = 1 - stdev(gm[-5:]) / mean(gm[-5:])
    得分 = GM_rank × 8 + GM_stability × 2
    简化：无全市场分位数时用绝对值映射
    """
    if not income:
        return {"score": 0, "gm_median": 0, "missing_data": True}

    gms = []
    for inc in income[-years:]:
        if inc.revenue > 0:
            if inc.op_cost > 0:
                gm = (inc.revenue - inc.op_cost) / inc.revenue  # 真实毛利率
            else:
                gm = inc.operating_profit / inc.revenue  # fallback
            gms.append(max(-1, min(gm, 1)))

    if not gms:
        return {"score": 0, "gm_median": 0, "missing_data": True}

    gm_median = median(gms[-3:]) if len(gms) >= 3 else mean(gms)

    # GM 水平分（绝对值映射，无全市场分位数时）
    if gm_median >= 0.60:   gm_rank = 1.0
    elif gm_median >= 0.40: gm_rank = 0.8
    elif gm_median >= 0.25: gm_rank = 0.6
    elif gm_median >= 0.10: gm_rank = 0.4
    else:                   gm_rank = 0.2

    # 稳定性
    if len(gms) >= 3:
        try:
            gm_stability = max(0, 1 - stdev(gms) / (abs(mean(gms)) + 0.001))
        except:
            gm_stability = 0.5
    else:
        gm_stability = 0.5

    score = gm_rank * 8 + gm_stability * 2
    return {"score": round(score, 1), "gm_median": round(gm_median, 4), "gm_stability": round(gm_stability, 4)}


# ═══════════════════════════════════════════════════════════
#  维度 II：估值水平 (Value) — 30%
# ═══════════════════════════════════════════════════════════

def factor_ev_operating_earnings(
    quotes: list[DailyQuote], balance: list[BalanceSheet],
    income: list[IncomeStatement], industry_group: str = "其他"
) -> dict:
    """V1: EV / Operating Earnings（15%）

    EV = 总市值 + 总负债 - 现金 - 长投 × 0.5
    OpEarnings = 营业利润 + 折旧摊销 + 财务费用（简化：营业利润 + 利息支出）
    得分 = (15 - min(EV/OE, 15)) / 15 × 10
    """
    if not quotes or not balance or not income:
        return {"score": 0, "ev_oe": 999, "missing_data": True}

    # Filter empty future-year data (revenue > 0 guard)
    real_income = [i for i in income if i.revenue > 0]
    real_balance = [b for b in balance if b.total_assets > 0]
    if not real_income or not real_balance:
        return {"score": 0, "ev_oe": 999, "missing_data": True}

    latest_quote = quotes[-1]
    latest_balance = sorted(real_balance, key=lambda x: x.report_year, reverse=True)[0]
    latest_income = sorted(real_income, key=lambda x: x.report_year, reverse=True)[0]

    mcap = latest_quote.market_cap
    if mcap <= 0:
        return {"score": 0, "ev_oe": 999, "missing_data": True}

    ev = mcap + latest_balance.total_liabilities - latest_balance.cash - latest_balance.long_term_invest * 0.5
    if ev <= 0:
        return {"score": 0, "ev_oe": 999, "negative_ev": True}
    op_earnings = latest_income.operating_profit + latest_income.interest_expense

    if op_earnings <= 0:
        return {"score": 0, "ev_oe": 999, "negative_earnings": True}

    ev_oe = ev / op_earnings
    score = max(0, (15 - min(ev_oe, 15)) / 15 * 10)
    return {"score": round(score, 1), "ev_oe": round(ev_oe, 2)}


def factor_fcf_yield(quotes: list[DailyQuote], cashflow: list[CashFlowStatement]) -> dict:
    """V2: FCF Yield（10%）

    FCF = OCF - Capex
    FCF_Yield = FCF / 总市值
    得分 = min(FCF_Yield, 0.15) / 0.15 × 10
    """
    if not quotes or not cashflow:
        return {"score": 0, "fcf_yield": 0, "missing_data": True}

    # Filter empty future-year data (operating_cf != 0 guard)
    real_cf = [c for c in cashflow if c.operating_cf != 0 or c.capex != 0]
    if not real_cf:
        return {"score": 0, "fcf_yield": 0, "missing_data": True}

    latest_cf = sorted(real_cf, key=lambda x: x.report_year, reverse=True)[0]
    latest_quote = quotes[-1]

    fcf = latest_cf.operating_cf - abs(latest_cf.capex)
    mcap = latest_quote.market_cap

    if mcap <= 0:
        return {"score": 0, "fcf_yield": 0, "missing_data": True}

    fcf_yield = fcf / mcap
    score = min(max(fcf_yield, 0), 0.15) / 0.15 * 10
    return {"score": round(score, 1), "fcf_yield": round(fcf_yield, 4)}


def factor_pb_industry_adj(
    quotes: list[DailyQuote], industry_group: str = "其他", industry_median_pb: float = None
) -> dict:
    """V3: PB 行业调整（5%）

    因子值 = PB / 行业 PB 中位数
    得分 = max(0, 1 - 因子值) × 10
    """
    if not quotes:
        return {"score": 0, "missing_data": True}

    pb = quotes[-1].pb
    if pb <= 0:
        return {"score": 0, "pb": round(pb, 2), "missing_data": True}

    if industry_median_pb is None or industry_median_pb <= 0:
        # 无行业中位数时用绝对估值
        if pb < 1.0:   score = 9
        elif pb < 2.0: score = 7
        elif pb < 3.0: score = 5
        elif pb < 5.0: score = 3
        else:          score = 1
        return {"score": score, "pb": round(pb, 2), "no_industry_median": True}

    factor_val = pb / industry_median_pb
    score = max(0, 1 - factor_val) * 10
    return {"score": round(score, 1), "pb": round(pb, 2), "industry_median_pb": round(industry_median_pb, 2)}


# ═══════════════════════════════════════════════════════════
#  维度 III：财务健康 (Health) — 20%
# ═══════════════════════════════════════════════════════════

def factor_leverage(balance: list[BalanceSheet], is_financial: bool = False) -> dict:
    """H1: 杠杆率（8%）

    debt_ratio = 总负债 / 总资产
    得分 = max(0, 1 - debt_ratio / 0.70) × 10
    金融行业跳过
    """
    if is_financial:
        return {"score": 5, "skipped_financial": True}

    if not balance:
        return {"score": 0, "missing_data": True}

    # Filter empty future-year data (total_assets > 0 guard)
    real_balance = [b for b in balance if b.total_assets > 0]
    if not real_balance:
        return {"score": 0, "missing_data": True}

    latest = sorted(real_balance, key=lambda x: x.report_year, reverse=True)[0]
    if latest.total_assets <= 0:
        return {"score": 0, "missing_data": True}

    debt_ratio = latest.total_liabilities / latest.total_assets
    score = max(0, 1 - debt_ratio / 0.70) * 10
    return {"score": round(score, 1), "debt_ratio": round(debt_ratio, 4)}


def factor_interest_coverage(income: list[IncomeStatement], is_financial: bool = False) -> dict:
    """H2: 利息覆盖倍数（7%）

    IC = (营业利润 + 财务费用) / 利息支出
    得分 = min(IC, 20) / 20 × 10
    金融行业跳过（利息支出是主营成本，非风险指标）
    """
    if is_financial:
        return {"score": 5, "skipped_financial": True}

    if not income:
        return {"score": 0, "missing_data": True}

    # Filter empty future-year data (revenue > 0 guard)
    real_income = [i for i in income if i.revenue > 0]
    if not real_income:
        return {"score": 0, "missing_data": True}

    latest = sorted(real_income, key=lambda x: x.report_year, reverse=True)[0]
    if latest.interest_expense <= 0:
        # 无利息支出 = 无有息负债 = 安全
        return {"score": 10, "ic": 999, "no_interest_expense": True}

    ic = (latest.operating_profit + latest.interest_expense) / latest.interest_expense
    score = min(ic, 20) / 20 * 10
    return {"score": round(score, 1), "ic": round(ic, 2)}


def factor_asset_quality(income: list[IncomeStatement], balance: list[BalanceSheet]) -> dict:
    """H3: 资产质量（5%）

    检查：应收账款/营收 + 存货/总资产 + 商誉风险
    得分 = (AR_score + Inv_score + GW_score) / 3 × 10
    """
    if not income or not balance:
        return {"score": 0, "missing_data": True}

    # Filter empty future-year data (revenue > 0, total_assets > 0 guard)
    real_income = [i for i in income if i.revenue > 0]
    real_balance = [b for b in balance if b.total_assets > 0]
    if not real_income or not real_balance:
        return {"score": 0, "missing_data": True}

    latest_inc = sorted(real_income, key=lambda x: x.report_year, reverse=True)[0]
    latest_bal = sorted(real_balance, key=lambda x: x.report_year, reverse=True)[0]

    # 应收/营收
    if latest_inc.revenue > 0:
        ar_ratio = latest_bal.accounts_receivable / latest_inc.revenue
    else:
        ar_ratio = 0
    ar_score = max(0, 1 - ar_ratio / 0.5)

    # 存货/总资产
    if latest_bal.total_assets > 0:
        inv_ratio = latest_bal.inventory / latest_bal.total_assets
    else:
        inv_ratio = 0
    inv_score = max(0, 1 - inv_ratio / 0.4)

    # 商誉/净资产
    if latest_bal.equity > 0:
        gw_ratio = latest_bal.goodwill / latest_bal.equity
    else:
        gw_ratio = 0
    gw_score = max(0, 1 - gw_ratio / 0.3)

    score = (ar_score + inv_score + gw_score) / 3 * 10
    return {"score": round(score, 1),
            "ar_ratio": round(ar_ratio, 4),
            "inv_ratio": round(inv_ratio, 4),
            "gw_ratio": round(gw_ratio, 4),
            "sub_scores": {"ar": round(ar_score * 10, 1),
                           "inv": round(inv_score * 10, 1),
                           "gw": round(gw_score * 10, 1)}}


# ═══════════════════════════════════════════════════════════
#  维度 IV：治理与安全边际 (Governance) — 15%
# ═══════════════════════════════════════════════════════════

def factor_shareholder_return(data: FinancialData) -> dict:
    """G1: 股东回报（8%）

    分红率 + 回购检测
    得分 = min(dividend_yield, 5%) / 5% × 7 + (3 if buyback else 0)
    """
    dy = data.dividend_yield
    if dy <= 0:
        return {"score": 0, "dividend_yield": dy, "no_dividend": True}

    div_score = min(dy, 0.05) / 0.05 * 7

    # 回购检测：equity 缩减 > 2%
    buyback = False
    real_balance = [b for b in data.balance if b.total_assets > 0]
    if len(real_balance) >= 2:
        sorted_bal = sorted(real_balance, key=lambda x: x.report_year, reverse=True)
        if sorted_bal[1].equity > 0:
            if sorted_bal[0].equity < sorted_bal[1].equity * 0.98:
                buyback = True

    score = div_score + (3 if buyback else 0)
    return {"score": round(score, 1), "dividend_yield": round(dy, 4), "buyback": buyback}


def factor_audit_governance(data: FinancialData) -> dict:
    """G2: 审计与治理（7%）

    得分 = 审计意见 + 四大审计 + 内部人持股（简化：按审计意见给分）
    """
    score = 0
    details = {}

    opinion_normalized = data.audit_opinion.replace('\u3000','').strip() if data.audit_opinion else ""
    if opinion_normalized in ("standard", "标准无保留意见", "标准无保留"):
        score += 5
        details["audit"] = "标准无保留"
    elif data.audit_opinion:
        score += 2
        details["audit"] = data.audit_opinion
    else:
        score += 3  # 无数据，默认给及格
        details["audit"] = "未知"

    # 简化：无 big4 和内部人持股数据，给基础分
    score += 2  # 基础治理得分

    return {"score": min(round(score, 1), 10), "details": details}


# ═══════════════════════════════════════════════════════════
#  综合因子计算入口
# ═══════════════════════════════════════════════════════════

def _weight_dimensions(
    quality_raw: float, value_raw: float, health_raw: float, gov_raw: float,
    weights: dict,
    v_sub_weights: tuple = (0.15, 0.10, 0.05),
    h_max: float = None,
    g_max: float = None,
) -> dict:
    """共享权重归一化 — 单点维护，CLI 和 cache 共用。

    Returns {"quality": N, "value": N, "health": N, "gov": N, "total": N}
    每个维度上限：Q≤50, V≤50, H≤30, G≤25
    v_sub_weights: (ev_w, fcf_w, pb_w) — 行业特殊规则可调整
    h_max, g_max: override for industries with skipped factors (e.g., financial)
    """
    q_max = 0.12 * 10 + 0.10 * 10 + 0.08 * 10 + 0.05 * 10  # = 3.5
    ev_w, fcf_w, pb_w = v_sub_weights
    v_max = ev_w * 10 + fcf_w * 10 + pb_w * 10  # = 3.0 default
    h_max = h_max if h_max is not None else 0.08 * 10 + 0.07 * 10 + 0.05 * 10  # = 2.0
    g_max = g_max if g_max is not None else 0.08 * 10 + 0.07 * 10  # = 1.5

    q_norm = quality_raw / q_max if q_max > 0 else 0
    v_norm = value_raw / v_max if v_max > 0 else 0
    h_norm = health_raw / h_max if h_max > 0 else 0
    g_norm = gov_raw / g_max if g_max > 0 else 0

    w = weights
    q_score = min(q_norm * w["quality"] * 100, 50)
    v_score = min(v_norm * w["value"] * 100, 50)
    h_score = min(h_norm * w["health"] * 100, 30)
    g_score = min(g_norm * w["gov"] * 100, 25)

    return {
        "quality": round(q_score, 1),
        "value": round(v_score, 1),
        "health": round(h_score, 1),
        "gov": round(g_score, 1),
        "total": round(q_score + v_score + h_score + g_score, 1),
    }


def compute_all_factors(data: FinancialData, industry_group: str = "其他") -> dict:
    """计算全部因子得分

    Returns:
        {
            "quality": {"roe": {...}, "roic": {...}, "cfo": {...}, "gm": {...}},
            "value": {"ev_oe": {...}, "fcf_yield": {...}, "pb_adj": {...}},
            "health": {"leverage": {...}, "ic": {...}, "asset_q": {...}},
            "gov": {"dividend": {...}, "audit": {...}},
            "weighted": {"quality": N, "value": N, "health": N, "gov": N, "total": N},
        }
    """
    from .industry import get_weights, get_special_rules

    weights = get_weights(industry_group)
    rules = get_special_rules(industry_group)
    is_financial = rules.get("skip_leverage", False)

    factors = {}

    # 维度 I：企业质量
    q_roe = factor_roe_stability(data.income, data.balance)
    q_roic = factor_roic(data.income, data.balance)
    q_cfo = factor_cfo_quality(data.income, data.cashflow)
    q_gm = factor_gross_margin_moat(data.income)
    factors["quality"] = {"roe": q_roe, "roic": q_roic, "cfo": q_cfo, "gm": q_gm}

    quality_raw = (
        q_roe.get("score", 0) * 0.12 +
        q_roic.get("score", 0) * 0.10 +
        q_cfo.get("score", 0) * 0.08 +
        q_gm.get("score", 0) * 0.05
    )

    # 维度 II：估值
    v_ev_oe = factor_ev_operating_earnings(data.quotes, data.balance, data.income, industry_group)
    v_fcf_y = factor_fcf_yield(data.quotes, data.cashflow)
    # Compute industry PB median from quotes if not cached
    industry_pb_median = _get_industry_pb_median(industry_group)
    v_pb = factor_pb_industry_adj(data.quotes, industry_group, industry_pb_median)
    factors["value"] = {"ev_oe": v_ev_oe, "fcf_yield": v_fcf_y, "pb_adj": v_pb}

    # ── V sub-factor weights (with industry special rules) ──
    ev_w, fcf_w, pb_w = 0.15, 0.10, 0.05
    ev_scale = rules.get("ev_weight_scale")
    pb_scale = rules.get("pb_weight_scale")
    if ev_scale is not None or pb_scale is not None:
        if ev_scale == 0:          # Financial: PB replaces EV entirely
            ev_w, pb_w = 0.0, 0.20
        elif pb_scale:             # Tech: PB weight reduced, EV absorbs freed weight
            pb_w = round(0.05 * pb_scale, 3)
            ev_w = round(0.15 + 0.05 - pb_w, 3)

    value_raw = (
        v_ev_oe.get("score", 0) * ev_w +
        v_fcf_y.get("score", 0) * fcf_w +
        v_pb.get("score", 0) * pb_w
    )

    # ── 方案C：质量调整估值 ──
    # V_有效 = V_raw × min(ROE_3y / 20%, 1)
    # 平庸公司便宜不给全额估值分，连续不武断
    roe_pairs_3y = []
    for inc in data.income[-3:]:
        bal = next((b for b in data.balance if b.report_year == inc.report_year), None)
        if bal and bal.equity > 0:
            roe_pairs_3y.append(inc.net_income / bal.equity)
    roe_3y = mean(roe_pairs_3y) if roe_pairs_3y else 0
    q_adj = min(roe_3y / 0.20, 1.0) if roe_3y > 0 else 0
    value_raw = value_raw * q_adj

    # 维度 III：财务健康
    h_lev = factor_leverage(data.balance, is_financial)
    h_ic = factor_interest_coverage(data.income, is_financial)
    h_aq = factor_asset_quality(data.income, data.balance)
    factors["health"] = {"leverage": h_lev, "ic": h_ic, "asset_q": h_aq}

    health_raw = (
        h_lev.get("score", 0) * 0.08 +
        h_ic.get("score", 0) * 0.07 +
        h_aq.get("score", 0) * 0.05
    )

    # 维度 IV：治理
    g_div = factor_shareholder_return(data)
    g_audit = factor_audit_governance(data)
    factors["gov"] = {"dividend": g_div, "audit": g_audit}

    # ── P3: Beneish M-Score 财务造假检测 ──
    # Use pre-TTM data (exclude current year): TTM partial-year data
    # corrupts YoY ratios like DSRI/GMI/AQI. Filter before computation.
    from datetime import datetime as _dt
    cur_year = _dt.now().year
    beneish_income = [i for i in data.income if i.report_year < cur_year]
    beneish_balance = [b for b in data.balance if b.report_year < cur_year]
    beneish_cf = [c for c in data.cashflow if c.report_year < cur_year]
    beneish_data = FinancialData(
        info=data.info,
        income=beneish_income,
        balance=beneish_balance,
        cashflow=beneish_cf,
        quotes=data.quotes,
        audit_opinion=data.audit_opinion,
        dividend_yield=data.dividend_yield,
        pledge_ratio=data.pledge_ratio,
        has_financial_fraud_penalty=data.has_financial_fraud_penalty,
    )
    beneish = compute_beneish_m_score(beneish_data)
    factors["beneish"] = beneish

    gov_raw = (
        g_div.get("score", 0) * 0.08 +
        g_audit.get("score", 0) * 0.07
    )
    
    # M-Score penalty: cap governance if manipulation detected
    if beneish.get("likely_manipulator"):
        factors["beneish_penalty"] = "likely_manipulator"
        gov_raw = min(gov_raw, 0.3)  # cap at 0.3/1.5 = 20% of gov max
    elif beneish.get("possible_manipulator"):
        factors["beneish_penalty"] = "possible_manipulator"
        gov_raw = min(gov_raw, 0.9)  # cap at 0.9/1.5 = 60% of gov max

    # 加权汇总
    # 各维度满分为：quality=35, value=30, health=20, gov=15
    # 行业权重调整这4个维度的占比
    # formula: dim_score = dim_raw/max_raw * weight * 100
    # Financial stocks have skipped leverage+IC factors → adjust h_max
    h_max_override = 0.08*5 + 0.07*5 + 0.05*10 if is_financial else None  # 1.25 vs default 2.0
    weighted = _weight_dimensions(quality_raw, value_raw, health_raw, gov_raw, weights, v_sub_weights=(ev_w, fcf_w, pb_w), h_max=h_max_override)

    factors["weighted"] = {
        "quality": round(weighted["quality"], 1),
        "value": round(weighted["value"], 1),
        "health": round(weighted["health"], 1),
        "gov": round(weighted["gov"], 1),
        "total": round(weighted["total"], 1),
    }

    return factors
