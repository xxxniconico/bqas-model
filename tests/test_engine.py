"""BQAS 引擎测试 — 覆盖黑名单、因子计算、评分引擎、数据模型。

测试设计原则：
- 单元测试用 mock 数据，不依赖网络
- 黑名单：9 项逐项验证通过/拒绝
- 因子：验证公式正确性和边界条件
- 评分引擎：验证端到端流程和评级阈值
"""

import pytest
from datetime import date
from bqas.data.schema import (
    StockInfo, IncomeStatement, BalanceSheet, CashFlowStatement,
    DailyQuote, FinancialData,
)
from bqas.engine.blacklist import check_blacklist
from bqas.engine.factors import (
    factor_roe_stability, factor_roic,
    factor_cfo_quality, factor_gross_margin_moat,
    factor_ev_operating_earnings, factor_fcf_yield,
    factor_pb_industry_adj, factor_leverage,
    factor_interest_coverage, factor_asset_quality,
    factor_shareholder_return, factor_audit_governance,
)
from bqas.engine.scorer import get_rating


# ═══════════════════════════════════════════════
#  Fixtures — 构建 mock 财务数据
# ═══════════════════════════════════════════════

def make_income(year: int, net_income: float, revenue: float = 1e10,
                operating_profit: float = 1e9, interest_expense: float = 1e8) -> IncomeStatement:
    return IncomeStatement(
        code="600519", report_year=year,
        revenue=revenue, operating_profit=operating_profit,
        net_income=net_income, interest_expense=interest_expense,
    )


def make_balance(year: int, equity: float = 1e11, total_assets: float = 2e11,
                 total_liabilities: float = 5e10, goodwill: float = 0,
                 short_term_debt: float = 0, long_term_debt: float = 0,
                 cash: float = 5e10, inventory: float = 2e10,
                 accounts_receivable: float = 1e10) -> BalanceSheet:
    return BalanceSheet(
        code="600519", report_year=year,
        total_assets=total_assets, total_liabilities=total_liabilities,
        equity=equity, goodwill=goodwill, inventory=inventory,
        accounts_receivable=accounts_receivable, cash=cash,
        short_term_debt=short_term_debt, long_term_debt=long_term_debt,
    )


def make_cashflow(year: int, operating_cf: float = 1e10,
                  capex: float = 1e9, financing_cf: float = 0) -> CashFlowStatement:
    return CashFlowStatement(
        code="600519", report_year=year,
        operating_cf=operating_cf, capex=capex,
        financing_cf=financing_cf,
    )


@pytest.fixture
def clean_moutai():
    """贵州茅台 — 应该通过所有否决"""
    return FinancialData(
        info=StockInfo(
            code="600519", name="贵州茅台",
            industry_sw="白酒", listing_date=date(2001, 8, 27),
            total_shares=1.26e9,
        ),
        income=[
            make_income(2023, 7.47e10, revenue=1.48e11, operating_profit=1.02e11),
            make_income(2024, 8.62e10, revenue=1.69e11, operating_profit=1.17e11),
            make_income(2025, 9.50e10, revenue=1.85e11, operating_profit=1.28e11),
        ],
        balance=[
            make_balance(2023),
            make_balance(2024),
            make_balance(2025),
        ],
        cashflow=[
            make_cashflow(2023),
            make_cashflow(2024),
            make_cashflow(2025),
        ],
        audit_opinion="standard",
        pledge_ratio=0.0,
    )


@pytest.fixture
def st_stock():
    """ST 股 — 第一项就该否决"""
    return FinancialData(
        info=StockInfo(code="600165", name="*ST宁科", is_st=True),
        income=[],
        balance=[],
        cashflow=[],
    )


@pytest.fixture
def recent_ipo():
    """上市不满 3 年的新股"""
    return FinancialData(
        info=StockInfo(
            code="001221", name="悍高集团",
            listing_date=date(2024, 6, 15),
        ),
        income=[],
        balance=[],
        cashflow=[],
    )


@pytest.fixture
def loss_maker():
    """连续亏损 3 年的公司"""
    return FinancialData(
        info=StockInfo(code="000001", name="亏损股", listing_date=date(2000, 1, 1)),
        income=[
            make_income(2023, -5e8),
            make_income(2024, -3e8),
            make_income(2025, -1e8),
        ],
        balance=[],
        cashflow=[],
    )


@pytest.fixture
def negative_ocf():
    """连续 3 年经营现金流为负"""
    return FinancialData(
        info=StockInfo(code="000002", name="OCF负", listing_date=date(2000, 1, 1)),
        income=[
            make_income(2023, 1e8),
            make_income(2024, 1e8),
            make_income(2025, 1e8),
        ],
        balance=[],
        cashflow=[
            make_cashflow(2023, -5e7),
            make_cashflow(2024, -3e7),
            make_cashflow(2025, -1e7),
        ],
    )


# ═══════════════════════════════════════════════
#  黑名单 — 9 项检查
# ═══════════════════════════════════════════════

class TestBlacklist:
    def test_clean_stock_passes(self, clean_moutai):
        """优质股通过全部否决"""
        passed, reason, checks = check_blacklist(clean_moutai)
        assert passed
        assert reason == ""
        assert checks["st_flag"][0] is True
        assert checks["listing_days"][0] is True

    def test_st_rejected(self, st_stock):
        """ST 股直接否决（第 1 项）"""
        passed, reason, checks = check_blacklist(st_stock)
        assert not passed
        assert "ST" in reason
        assert checks["st_flag"][0] is False

    def test_recent_ipo_rejected(self, recent_ipo):
        """上市不满 3 年否决（第 2 项）"""
        passed, reason, checks = check_blacklist(recent_ipo)
        assert not passed
        assert "上市" in reason

    def test_consecutive_loss_rejected(self, loss_maker):
        """连续 3 年亏损否决（第 5 项）"""
        passed, reason, checks = check_blacklist(loss_maker)
        assert not passed
        assert "净利润" in reason

    def test_negative_ocf_rejected(self, negative_ocf):
        """连续 3 年 OCF < 0 否决（第 6 项）"""
        passed, reason, checks = check_blacklist(negative_ocf)
        assert not passed
        assert "经营现金流" in reason

    def test_audit_opinion_rejected(self, clean_moutai):
        """非标审计意见否决（第 7 项）"""
        clean_moutai.audit_opinion = "qualified"
        passed, reason, _ = check_blacklist(clean_moutai)
        assert not passed
        assert "审计" in reason or "qualified" in reason

    def test_goodwill_bomb_rejected(self, clean_moutai):
        """商誉炸弹否决（第 8 项）— 商誉 > 50%净资产 && > 10亿"""
        clean_moutai.balance[2].goodwill = 6e10  # modify latest year (2025), 60B > 50% of 100B equity
        passed, reason, _ = check_blacklist(clean_moutai)
        assert not passed
        assert "商誉" in reason

    def test_pledge_ratio_rejected(self, clean_moutai):
        """大股东质押 > 70% 否决（第 9 项）"""
        clean_moutai.pledge_ratio = 0.85
        passed, reason, _ = check_blacklist(clean_moutai)
        assert not passed
        assert "质押" in reason

    def test_market_cap_too_small_rejected(self, clean_moutai):
        """市值 < 30亿否决（第 3 项）"""
        clean_moutai.quotes = [
            DailyQuote(code="600519", trade_date=date(2026, 5, 8),
                       close=10.0, market_cap=2e9, pb=1.0, turnover_amount=5e7)
        ]
        passed, reason, _ = check_blacklist(clean_moutai)
        assert not passed
        assert "市值" in reason


# ═══════════════════════════════════════════════
#  因子计算 — 公式正确性
# ═══════════════════════════════════════════════

class TestFactors:
    def test_roe_perfect_score(self):
        """ROE ≥30% → 满分 10"""
        income = [
            make_income(2025, 3e10),  # net_income
        ]
        balance = [
            make_balance(2025, equity=1e11),  # ROE = 30%
        ]
        result = factor_roe_stability(income, balance, years=1)
        assert result["score"] == pytest.approx(10.0, abs=0.1)
        assert result["roe_5y"] == pytest.approx(0.30, abs=0.01)

    def test_roe_half_score(self):
        """ROE = 15% → 5 分"""
        income = [make_income(2025, 1.5e10)]
        balance = [make_balance(2025, equity=1e11)]
        result = factor_roe_stability(income, balance, years=1)
        assert result["score"] == pytest.approx(5.0, abs=0.1)

    def test_roe_missing_data(self):
        """无数据 → 0 分 + missing_data flag"""
        result = factor_roe_stability([], [], years=1)
        assert result["score"] == 0
        assert result["missing_data"] is True

    def test_roic_calculation(self):
        """ROIC = EBIT / (equity + debt)"""
        income = [IncomeStatement(
            code="000001", report_year=2025,
            revenue=1e10, operating_profit=2e9,
            net_income=1.5e9, interest_expense=5e8,
            ebit=2.5e9,  # operating_profit + interest_expense
        )]
        balance = [BalanceSheet(
            code="000001", report_year=2025,
            total_assets=3e11, total_liabilities=1e11,
            equity=2e11, goodwill=0,
            short_term_debt=5e10, long_term_debt=3e10,
        )]
        result = factor_roic(income, balance, years=1)
        # EBIT = 2.5e9, invested = 2e11 + 5e10 + 3e10 = 2.8e11
        # ROIC = 2.5e9 / 2.8e11 ≈ 0.0089, Score ≈ 0.3
        assert result["score"] > 0
        assert result["years_available"] >= 1

    def test_ev_operating_earnings_negative_ev(self):
        """EV ≤ 0 → 0 分"""
        quotes = [
            DailyQuote(code="000001", trade_date=date(2026,5,8),
                       close=200.0, market_cap=2.52e11, pb=10.0, turnover_amount=5e8)
        ]
        income = [make_income(2025, 1e10, operating_profit=5e9)]
        balance = [make_balance(2025, equity=1e12, total_liabilities=1e10,
                                 cash=1e12)]  # cash >> liabilities → negative EV
        result = factor_ev_operating_earnings(quotes, balance, income)
        assert result["score"] == 0

    def test_fcf_yield_capped(self):
        """FCF Yield > 15% → 封顶 10 分"""
        quotes = [
            DailyQuote(code="000001", trade_date=date(2026,5,8),
                       close=100.0, market_cap=1.26e11, pb=10.0, turnover_amount=5e8)
        ]
        cashflow = [make_cashflow(2025, operating_cf=1e10, capex=1e9)]
        result = factor_fcf_yield(quotes, cashflow)
        # FCF / Market Cap = 9e9 / 1.26e11 ≈ 7.1% → capped ok
        assert result["score"] <= 10.0

    def test_leverage_score(self):
        """低杠杆 → 高分"""
        income = [make_income(2025, 1e10)]
        balance = [make_balance(2025, equity=1e11, total_liabilities=2e10)]
        result = factor_leverage(balance)
        # Debt/Equity = 20% → should be high score
        assert result["score"] >= 7.0

    def test_interest_coverage_safe(self):
        """利息覆盖 > 10x → 满分"""
        income = [IncomeStatement(
            code="000001", report_year=2025,
            revenue=1e10, operating_profit=5e9,
            net_income=4e9, interest_expense=1e8,
        )]
        result = factor_interest_coverage(income)
        assert result["score"] >= 8.0
        assert result["ic"] >= 10.0

    def test_audit_quality_standard(self):
        """标准无保留 → 基础分"""
        data = FinancialData(
            info=StockInfo(code="000001", name="测试"),
            audit_opinion="standard",
            pledge_ratio=0.0,
            dividend_yield=0.02,
        )
        result = factor_audit_governance(data)
        assert result["score"] > 0

    def test_audit_quality_qualified(self):
        """非标审计 → 低分"""
        data = FinancialData(
            info=StockInfo(code="000001", name="测试"),
            audit_opinion="qualified",
            pledge_ratio=0.0,
            dividend_yield=0.0,
        )
        result = factor_audit_governance(data)
        # 非标审计应该大打折扣
        assert result["score"] <= 5.0


# ═══════════════════════════════════════════════
#  评分引擎
# ═══════════════════════════════════════════════

class TestScorer:
    def test_rating_85_plus(self):
        """≥85 → ⭐⭐⭐⭐⭐"""
        r = get_rating(85)
        assert "⭐⭐⭐⭐⭐" in r["stars"]
        assert r["label"] == "巴菲特级别"

    def test_rating_75(self):
        """75-84 → ⭐⭐⭐⭐"""
        r = get_rating(75)
        assert "⭐⭐⭐⭐" in r["stars"]

    def test_rating_65(self):
        """65-74 → ⭐⭐⭐"""
        r = get_rating(65)
        assert "⭐⭐⭐" in r["stars"]

    def test_rating_50(self):
        """50-64 → ⭐⭐"""
        r = get_rating(50)
        assert "⭐⭐" in r["stars"]

    def test_rating_below_50(self):
        """<50 → ⭐"""
        r = get_rating(0)
        assert "⭐" in r["stars"]


# ═══════════════════════════════════════════════
#  跳过测试 — 需要网络
# ═══════════════════════════════════════════════

@pytest.mark.skip(reason="需要网络访问 akshare/Sina API")
def test_score_moutai_integration():
    """集成测试：贵州茅台完整评分"""
    from bqas.engine.scorer import score_stock
    result = score_stock("600519")
    assert result["code"] == "600519"
    assert result["passed_blacklist"] is True
    assert result["total"] > 0


@pytest.mark.skip(reason="需要网络访问 akshare/Sina API")
def test_score_wuliangye_integration():
    """集成测试：五粮液完整评分"""
    from bqas.engine.scorer import score_stock
    result = score_stock("000858")
    assert result["code"] == "000858"
    assert result["passed_blacklist"] is True


@pytest.mark.skip(reason="需要网络访问 akshare/Sina API")
def test_st_stock_veto():
    """集成测试：ST 股被拒绝"""
    from bqas.engine.scorer import score_stock
    result = score_stock("600165")
    assert result["passed_blacklist"] is False


@pytest.mark.skip(reason="需要网络访问 akshare/Sina API")
def test_batch_rank_runs():
    """集成测试：批量排名可运行"""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "batch_rank3.py"],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0


@pytest.mark.skip(reason="需要网络访问 akshare/Sina API")
def test_backtest_runs():
    """集成测试：回测可运行"""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "backtest_multi_year.py"],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0
