#!/usr/bin/env python3
"""BQAS — Streamlit Cloud: 嵌入 dashboard.html，数据来自 factor_cache.json + DB 直读"""
import json, sqlite3
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="BQAS 巴菲特量化", page_icon="📊", layout="wide")

BASE = Path(__file__).parent
html = (BASE / "dashboard.html").read_text(encoding="utf-8")

# ── Build data from factor_cache + DB ──
cache = json.loads((BASE / "bqas" / "data" / "cache" / "factor_cache.json").read_text())
db_path = str(BASE / "bqas" / "data" / "cache" / "bqas.db")
factors = cache.get("factors", {})
blacklist = cache.get("blacklist", {})

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Read latest year data for V dimension
import pandas as pd, numpy as np
balance = pd.read_sql("SELECT code, total_assets, total_liabilities, equity, cash_equiv, long_term_invest FROM balance WHERE report_period='20251231'", conn)
cf = pd.read_sql("SELECT code, operating_cf, capex FROM cashflow WHERE report_period='20251231'", conn)
income_v = pd.read_sql("SELECT code, operating_profit, interest_expense FROM income WHERE report_period='20251231'", conn)
quotes = pd.read_sql("SELECT code, close, pb FROM quotes WHERE trade_date=(SELECT MAX(trade_date) FROM quotes)", conn)
stocks = pd.read_sql("SELECT code, name, total_shares, industry_sw, listing_date, is_st FROM stock_info", conn)
conn.close()

# Merge
vdf = balance.merge(cf, on='code', how='left').merge(income_v, on='code', how='left')
vdf = vdf.merge(quotes, on='code', how='left').merge(stocks, on='code', how='left')
for c in ['close','pb','total_shares','operating_cf','capex','total_assets','total_liabilities','equity','cash_equiv','long_term_invest','operating_profit','interest_expense']:
    if c in vdf.columns:
        vdf[c] = pd.to_numeric(vdf[c], errors='coerce').fillna(0)

vdf['market_cap'] = vdf['close'] * vdf['total_shares']
vdf['ev'] = np.where((vdf['market_cap']>0) & (vdf['total_assets']>1e6),
    vdf['market_cap'] + vdf['total_liabilities'] - vdf['cash_equiv'] - vdf['long_term_invest'] * 0.5, np.nan)
vdf['op_earnings'] = vdf['operating_profit'] + vdf['interest_expense'].abs()
vdf['ev_oe'] = np.where((vdf['ev'].notna()) & (vdf['ev']>1e6) & (vdf['op_earnings']>1e6),
    vdf['ev'] / vdf['op_earnings'], 999)
vdf['evoe_score'] = np.where(vdf['ev_oe']<999, np.clip((15 - vdf['ev_oe'].clip(upper=15)) / 15 * 10, 0, 10), 0)
fcf = vdf['operating_cf'] - vdf['capex'].abs()
vdf['fcf_yield_raw'] = np.where(vdf['market_cap']>1e6, fcf / vdf['market_cap'], 0)
vdf['fcf_yield'] = vdf['fcf_yield_raw'].clip(0, 0.15)
vdf['fcf_score'] = np.clip(vdf['fcf_yield'] / 0.15 * 10, 0, 10)
vdf['pb_score'] = np.clip((1 - vdf['pb'] / 2.0) * 10, 0, 10)
vdf['v_score'] = ((vdf['evoe_score'] * 0.50 + vdf['fcf_score'] * 0.333 + vdf['pb_score'] * 0.167) * 3.0).round(1)
v_lookup = dict(zip(vdf['code'].astype(str).str.zfill(6), vdf['v_score'].round(1)))

# Filter: market_cap >= 30B or total_assets >= 100B, listed >3 years
import time
valid_mcap = set(vdf[(vdf['market_cap']>=3e10)|(vdf['total_assets']>=1e11)]['code'].astype(str).str.zfill(6))
stocks['listing_parsed'] = pd.to_datetime(stocks['listing_date'], format='mixed', errors='coerce')
stocks['listing_sec'] = stocks['listing_parsed'].astype('int64') // 10**9
valid_listed = set(stocks[stocks['listing_sec'].notna()][stocks['listing_sec'] < time.time() - 3*365*24*3600]['code'].astype(str).str.zfill(6))
valid_codes = valid_mcap & valid_listed

# Build screener
screener = []
industry_map = dict(zip(stocks['code'].astype(str).str.zfill(6), stocks['industry_sw']))
name_map = dict(zip(stocks['code'].astype(str).str.zfill(6), stocks['name']))

for code, f in factors.items():
    if code in blacklist: continue
    if f.get('q_weighted', 0) <= 0: continue
    if code not in valid_codes: continue
    q = f.get('q_weighted', 0)
    h = f.get('h_weighted', 0)
    g = f.get('g_weighted', 0)
    v = v_lookup.get(code, 0)
    total = round(q + v + h + g, 1)
    roe = round(f.get('roe_3y', 0) * 100, 1)
    screener.append({
        'code': code, 'name': name_map.get(code, code),
        'total': total, 'q_score': round(q,1), 'v_score': round(v,1),
        'h_score': round(h,1), 'g_score': round(g,1), 'roe_pct': roe,
        'industry_sw': industry_map.get(code, '未知'),
    })
screener.sort(key=lambda x: x['total'], reverse=True)

# Search index
search_index = [{'code': s['code'], 'name': s['name'], 'industry': s['industry_sw'], 'market': 'cn'} for s in screener[:200]]

# Industry summary
from collections import Counter
ind = Counter(s['industry_sw'] for s in screener[:50])
industry_summary = [{'industry': k, 'count': v, 'avg_score': round(sum(s['total'] for s in screener[:50] if s['industry_sw']==k)/v, 1)} for k, v in ind.most_common(15)]

# Full score data for top 30
score_data = {}
for s in screener[:30]:
    score_data[s['code']] = {
        'code': s['code'], 'name': s['name'],
        'total': s['total'], 'rating': '⭐⭐⭐', 'rating_label': '优秀',
        'industry_sw': s['industry_sw'], 'industry_group': s['industry_sw'],
        'passed_blacklist': True, 'blacklist_reason': '',
        'blacklist_checks': {},
        'scores': {'weighted': {'quality': s['q_score'], 'value': s['v_score'], 'health': s['h_score'], 'gov': s['g_score'], 'total': s['total']}},
        'beneish': {'m_score': 0, 'likely_manipulator': False, 'possible_manipulator': False},
        'factors': {'roe_3y': s['roe_pct'] / 100 if s['roe_pct'] else 0},
    }

data_json = json.dumps({
    'health': {'status': 'ok', 'stocks': len(stocks), 'cache': {'cached_stocks': len(screener), 'built_at': '2026-05-15'}},
    'screener': screener[:50],
    'industry_summary': industry_summary,
    'search_index': search_index,
    'score_data': score_data,
    'compare': [score_data.get(s['code']) for s in screener[:5] if s['code'] in score_data],
}, ensure_ascii=False)

# Inject into HTML
injection = f"""<script>
window._BQAS = {data_json};
(function(){{
    var _fetch = window.fetch;
    var D = window._BQAS;
    window.fetch = function(url, opts) {{
        var u = String(url);
        if (u.indexOf('/api/health') >= 0) return Promise.resolve({{json:function(){{return Promise.resolve(D.health||{{status:'ok'}});}}}});
        if (u.indexOf('/api/screener') >= 0) return Promise.resolve({{json:function(){{return Promise.resolve(D.screener||[]);}}}});
        if (u.indexOf('/api/industry-summary') >= 0) return Promise.resolve({{json:function(){{return Promise.resolve(D.industry_summary||[]);}}}});
        if (u.indexOf('/api/search') >= 0) {{
            var q = (new URL(u,'http://x').searchParams.get('q')||'').toLowerCase();
            var results = (D.search_index||[]).filter(function(s){{return s.code.indexOf(q)>=0||s.name.toLowerCase().indexOf(q)>=0||s.industry.indexOf(q)>=0;}});
            return Promise.resolve({{json:function(){{return Promise.resolve(results);}}}});
        }}
        if (u.indexOf('/api/compare') >= 0) {{
            var codes = (u.split('codes=')[1]||'').split('&')[0].split(',');
            return Promise.resolve({{json:function(){{return Promise.resolve((D.compare||[]).filter(function(s){{return codes.indexOf(s.code)>=0;}}));}}}});
        }}
        if (u.indexOf('/api/score/') >= 0) {{
            var code = u.split('/').pop().split('?')[0];
            return Promise.resolve({{json:function(){{return Promise.resolve((D.score_data||{{}})[code]||{{error:'未找到'}});}}}});
        }}
        if (u.indexOf('/api/stock-profile/') >= 0) {{
            var code = u.split('/').pop();
            return Promise.resolve({{json:function(){{return Promise.resolve((D.score_data||{{}})[code]||{{}});}}}});
        }}
        return _fetch.call(window, url, opts);
    }};
}})();
</script>
"""
html = html.replace('</head>', injection + '\n</head>')
st.components.v1.html(html, height=2400, scrolling=True)
