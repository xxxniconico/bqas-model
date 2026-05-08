import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bqas.data.fetcher import Fetcher
from bqas.engine.factors import FactorEngine
from bqas.engine.blacklist import Blacklist
from bqas.engine.ranker import Ranker
from bqas.engine.ranker import compute_industry_means
import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.progress import track

console = Console()
fetcher = Fetcher()
factors = FactorEngine()
blacklist = Blacklist()

# Fetch all stocks with valid data
all_stocks = pd.read_sql(
    'SELECT DISTINCT stock_code FROM financials WHERE equity > 1000000 AND total_assets > 1000000 AND revenue > 1000000',
    fetcher.db_conn
)['stock_code'].tolist()
console.print(f'[dim]Valid stocks: {len(all_stocks)}[/dim]')

# Get industry means for neutralization
industry_means = compute_industry_means(fetcher.db_conn)

ranker = Ranker(fetcher, factors, blacklist, neutralize=True, industry_means=industry_means)

# Score all
results = []
for code in track(all_stocks, description='Scoring...'):
    result = ranker.score_stock(code)
    if result['total_score'] >= 0 and result.get('passed_blacklist', True):
        results.append(result)

# Sort and take top 30
results.sort(key=lambda x: x['total_score'], reverse=True)
top30 = results[:30]

# Display
table = Table(title='BQAS 全市场 Top 30（中性化 + EV/FCF修复 + 三层过滤）')
table.add_column('#', style='dim')
table.add_column('股票', style='cyan')
table.add_column('总分', style='yellow')
table.add_column('行业', style='green')
table.add_column('质量', style='magenta')
table.add_column('估值', style='blue')
table.add_column('健康', style='red')
table.add_column('治理', style='dim')

for i, r in enumerate(top30, 1):
    table.add_row(
        str(i),
        f"{r['name']} ({r['code']})",
        f"{r['total_score']:.1f}",
        r.get('industry', '?'),
        f"{r.get('quality_score', 0):.1f}",
        f"{r.get('valuation_score', 0):.1f}",
        f"{r.get('financial_health_score', 0):.1f}",
        f"{r.get('governance_score', 0):.1f}",
    )

console.print(table)
console.print(f'[dim]Total scored: {len(results)}, Top 30 range: {top30[-1]["total_score"]:.1f} - {top30[0]["total_score"]:.1f}[/dim]')
