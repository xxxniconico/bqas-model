"""
Beneish M-Score — 财务造假检测 (P3)

8-ratio model for detecting earnings manipulation that standard
audit opinions miss (channel stuffing, cost capitalization, etc.).

参考: Beneish (1999) "The Detection of Earnings Manipulation"

Implementation notes:
  - Uses available financial statement data from SQLite cache
  - Some ratios use approximations where exact fields missing
    (e.g., GM ≈ operating_profit/revenue instead of true gross margin)
  - DEPI skipped (no depreciation data available)
  - SGAI approximated with (revenue - operating_profit) proxy

Scoring:
  M-Score > -1.78 → likely manipulator (flag in governance)
  M-Score > -2.22 → possible manipulator (warning)
  M-Score ≤ -2.22 → clean
"""

import logging
from ..data.schema import FinancialData

logger = logging.getLogger(__name__)


def compute_beneish_m_score(data: FinancialData) -> dict:
    """Compute Beneish M-Score for a single stock.

    Requires at least 2 consecutive years of income + balance data.

    Returns:
        {
            "m_score": float,       # Beneish M-Score
            "likely_manipulator": bool,  # M > -1.78
            "possible_manipulator": bool, # M > -2.22
            "ratios": {             # individual 8-ratio values
                "dsri": float, "gmi": float, "aqi": float,
                "sgi": float, "tata": float, "lvgi": float,
            },
            "flags": [str],         # which ratios triggered
            "missing_data": bool,
        }
    """
    income = sorted(data.income, key=lambda x: x.report_year)
    balance = sorted(data.balance, key=lambda x: x.report_year)
    cashflow = sorted(data.cashflow, key=lambda x: x.report_year)

    # Filter out periods with zero revenue (no data yet)
    income = [i for i in income if i.revenue > 0]
    balance = [b for b in balance if b.total_assets > 0]
    cashflow = [c for c in cashflow if c.operating_cf != 0 or c.capex != 0]

    if len(income) < 2 or len(balance) < 2:
        return {
            "m_score": 0, "likely_manipulator": False,
            "possible_manipulator": False, "ratios": {},
            "flags": [], "missing_data": True,
        }

    t = income[-1]   # current year
    t1 = income[-2]  # prior year
    b_t = balance[-1]
    b_t1 = balance[-2]
    cf_t = cashflow[-1] if cashflow else None

    ratios = {}
    flags = []

    # ── 1. DSRI: Days Sales in Receivables Index ──
    ar_t = b_t.accounts_receivable
    ar_t1 = b_t1.accounts_receivable
    if ar_t1 > 0 and t1.revenue > 0:
        dsri = (ar_t / max(t.revenue, 1)) / (ar_t1 / t1.revenue)
        ratios["dsri"] = round(dsri, 4)
        if dsri > 1.2:
            flags.append(f"DSRI={dsri:.2f} (应收账款增速异常)")
    else:
        ratios["dsri"] = 1.0

    # ── 2. GMI: Gross Margin Index ──
    if t1.revenue > 0 and t.revenue > 0:
        gm_t = t.operating_profit / t.revenue
        gm_t1 = t1.operating_profit / t1.revenue
        if gm_t > 0:
            gmi = gm_t1 / gm_t
            ratios["gmi"] = round(gmi, 4)
            if gmi > 1.2:
                flags.append(f"GMI={gmi:.2f} (毛利率恶化)")
        else:
            ratios["gmi"] = 1.0
    else:
        ratios["gmi"] = 1.0

    # ── 3. AQI: Asset Quality Index (simplified) ──
    ca_t = b_t.cash + b_t.inventory + b_t.accounts_receivable
    ca_t1 = b_t1.cash + b_t1.inventory + b_t1.accounts_receivable
    if b_t1.total_assets > 0:
        nca_ratio_t = 1 - (ca_t / max(b_t.total_assets, 1))
        nca_ratio_t1 = 1 - (ca_t1 / b_t1.total_assets)
        if nca_ratio_t1 > 0:
            aqi = nca_ratio_t / nca_ratio_t1
            ratios["aqi"] = round(aqi, 4)
            if aqi > 1.1:
                flags.append(f"AQI={aqi:.2f} (非流动资产占比上升)")
        else:
            ratios["aqi"] = 1.0
    else:
        ratios["aqi"] = 1.0

    # ── 4. SGI: Sales Growth Index ──
    if t1.revenue > 0:
        sgi = t.revenue / t1.revenue
        ratios["sgi"] = round(sgi, 4)
        if sgi > 1.3:
            flags.append(f"SGI={sgi:.2f} (营收增速过高)")
    else:
        ratios["sgi"] = 1.0

    # ── 5. TATA: Total Accruals to Total Assets ──
    if cf_t and b_t.total_assets > 0:
        total_accruals = t.net_income - cf_t.operating_cf
        tata = total_accruals / b_t.total_assets
        ratios["tata"] = round(tata, 4)
        if tata > 0.05:
            flags.append(f"TATA={tata:.3f} (应计利润过高)")
        elif tata < -0.05:
            flags.append(f"TATA={tata:.3f} (大幅负应计)")
    else:
        ratios["tata"] = 0

    # ── 6. LVGI: Leverage Index ──
    if b_t1.total_assets > 0:
        lev_t = (b_t.long_term_debt + b_t.short_term_debt) / max(b_t.total_assets, 1)
        lev_t1 = (b_t1.long_term_debt + b_t1.short_term_debt) / b_t1.total_assets
        if lev_t1 > 0:
            lvgi = lev_t / lev_t1
            ratios["lvgi"] = round(lvgi, 4)
            if lvgi > 1.2:
                flags.append(f"LVGI={lvgi:.2f} (杠杆率上升)")
        else:
            ratios["lvgi"] = 1.0
    else:
        ratios["lvgi"] = 1.0

    # ── M-Score Calculation (6-ratio simplified) ──
    m_score = (
        -4.84
        + 0.920 * ratios.get("dsri", 1.0)
        + 0.528 * ratios.get("gmi", 1.0)
        + 0.404 * ratios.get("aqi", 1.0)
        + 0.892 * ratios.get("sgi", 1.0)
        + 4.679 * ratios.get("tata", 0)
        - 0.327 * ratios.get("lvgi", 1.0)
    )

    return {
        "m_score": round(m_score, 3),
        "likely_manipulator": m_score > -1.78,
        "possible_manipulator": m_score > -2.22,
        "ratios": ratios,
        "flags": flags,
        "missing_data": False,
    }
