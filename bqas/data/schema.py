"""BQAS 数据模型定义

所有财务数据的 Pydantic schema，确保类型安全。
"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class StockInfo(BaseModel):
    """股票基础信息"""
    code: str = Field(..., description="股票代码，如 '600519'")
    name: str = Field(..., description="股票名称")
    industry_sw: str = Field(default="", description="申万一级行业")
    listing_date: Optional[date] = Field(None, description="上市日期")
    total_shares: float = Field(default=0, description="总股本（股）")
    is_st: bool = Field(default=False)


class IncomeStatement(BaseModel):
    """利润表（年度）"""
    code: str
    report_year: int
    revenue: float = 0.0            # 营业收入
    operating_profit: float = 0.0   # 营业利润
    net_income: float = 0.0         # 净利润
    interest_expense: float = 0.0   # 利息支出
    ebit: float = 0.0               # 息税前利润


class BalanceSheet(BaseModel):
    """资产负债表（年度）"""
    code: str
    report_year: int
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    equity: float = 0.0             # 股东权益
    goodwill: float = 0.0           # 商誉
    inventory: float = 0.0          # 存货
    accounts_receivable: float = 0.0  # 应收账款
    cash: float = 0.0               # 现金及等价物
    long_term_invest: float = 0.0   # 长期投资
    short_term_debt: float = 0.0    # 短期借款
    long_term_debt: float = 0.0     # 长期借款


class CashFlowStatement(BaseModel):
    """现金流量表（年度）"""
    code: str
    report_year: int
    operating_cf: float = 0.0       # 经营活动现金流
    capex: float = 0.0              # 购建固定资产支出
    financing_cf: float = 0.0       # 筹资活动现金流


class DailyQuote(BaseModel):
    """日行情"""
    code: str
    trade_date: date
    close: float
    market_cap: float = 0.0         # 总市值（亿元→元）
    pb: float = 0.0                 # 市净率
    turnover_amount: float = 0.0    # 成交额（元）


class FinancialData(BaseModel):
    """单只股票的完整财务数据"""
    info: StockInfo
    income: list[IncomeStatement] = []
    balance: list[BalanceSheet] = []
    cashflow: list[CashFlowStatement] = []
    quotes: list[DailyQuote] = []
    audit_opinion: str = ""         # 最新审计意见
    pledge_ratio: float = 0.0       # 大股东质押比例
    dividend_yield: float = 0.0     # 股息率


class BlacklistResult(BaseModel):
    """一票否决检查结果"""
    passed: bool
    reason: str = ""
    checks: dict = {}
