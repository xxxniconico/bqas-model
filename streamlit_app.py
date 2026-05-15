#!/usr/bin/env python3
"""BQAS — Streamlit Cloud: 嵌入 dashboard.html + factor_cache + DB V维度实时计算 + score_stock() for 30预选股票"""
import sys, json, sqlite3, time, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
import streamlit as st

from bqas.engine.scorer import score_stock
from bqas.engine.beneish import compute_beneish_m_score
from bqas.data.fetcher import fetch_financials
from bqas.engine.industry import get_industry_group

st.set_page_config(page_title="BQAS 巴菲特量化", page_icon="📊", layout="wide")

CACHE_PATH = Path(__file__).parent / "bqas" / "data" / "cache" / "factor_cache.json"
DB_PATH = Path(__file__).parent / "bqas" / "data" / "cache" / "bqas.db"
HTML_PATH = Path(__file__).parent / "dashboard.html"

html_template = HTML_PATH.read_text(encoding="utf-8")


def build_screener_data() -> list[dict]:
    cache = json.loads(CACHE_PATH.read_text())
    factors = cache.get("factors", {})
    blacklist = cache.get("blacklist", {})

    if not factors:
        return []

    conn = sqlite3.connect(str(DB_PATH))

    balance_df = pd.read_sql(
        "SELECT code, total_assets, total_liabilities, equity, cash_equiv, long_term_invest FROM balance WHERE report_period='20251231'",
        conn
    )
    cf_df = pd.read_sql(
        "SELECT code, operating_cf, capex FROM cashflow WHERE report_period='20251231'",
        conn
    )
    quotes_df = pd.read_sql(
        "SELECT code, close, pb FROM quotes WHERE trade_date=(SELECT MAX(trade_date) FROM quotes)",
        conn
    )
    stock_df = pd.read_sql(
        "SELECT code, total_shares, is_st, industry_sw, listing_date FROM stock_info",
        conn
    )
    income_df = pd.read_sql(
        "SELECT code, operating_profit, interest_expense FROM income WHERE report_period='20251231'",
        conn
    )
    conn.close()

    vdf = balance_df.merge(cf_df, on='code', how='left').merge(quotes_df, on='code', how='left')
    vdf = vdf.merge(stock_df, on='code', how='left').merge(income_df, on='code', how='left')
    for c in ['close', 'pb', 'total_shares', 'operating_cf', 'capex', 'total_assets',
              'total_liabilities', 'equity', 'cash_equiv', 'long_term_invest',
              'operating_profit', 'interest_expense']:
        if c in vdf.columns:
            vdf[c] = pd.to_numeric(vdf[c], errors='coerce').fillna(0)

    vdf['market_cap'] = vdf['close'] * vdf['total_shares']
    vdf['ev'] = np.where(
        (vdf['market_cap'] > 0) & (vdf['total_assets'] > 1e6),
        vdf['market_cap'] + vdf['total_liabilities'] - vdf['cash_equiv'] - vdf['long_term_invest'] * 0.5,
        np.nan
    )
    vdf['op_earnings'] = vdf['operating_profit'] + vdf['interest_expense'].abs()
    vdf['ev_oe'] = np.where(
        (vdf['ev'].notna()) & (vdf['ev'] > 1e6) & (vdf['op_earnings'] > 1e6),
        vdf['ev'] / vdf['op_earnings'],
        999
    )
    vdf['evoe_score'] = np.where(
        vdf['ev_oe'] < 999,
        np.clip((15 - vdf['ev_oe'].clip(upper=15)) / 15 * 10, 0, 10),
        0
    )

    fcf = vdf['operating_cf'] - vdf['capex'].abs()
    vdf['fcf_yield_raw'] = np.where(vdf['market_cap'] > 1e6, fcf / vdf['market_cap'], 0)
    vdf['fcf_yield'] = vdf['fcf_yield_raw'].clip(0, 0.15)
    vdf['fcf_score'] = np.clip(vdf['fcf_yield'] / 0.15 * 10, 0, 10)

    ind_pb = vdf[(vdf['pb'] > 0) & (vdf['pb'] < 100)].copy()
    ind_pb['ind_grp'] = ind_pb['industry_sw'].apply(lambda x: get_industry_group(x) if x else '其他')
    ind_medians = ind_pb.groupby('ind_grp')['pb'].median().to_dict()
    vdf['ind_grp'] = vdf['industry_sw'].apply(lambda x: get_industry_group(x) if x else '其他')
    vdf['ind_pb_median'] = vdf['ind_grp'].map(ind_medians).fillna(2.0)
    vdf['pb_factor'] = np.where(
        (vdf['pb'] > 0) & (vdf['ind_pb_median'] > 0),
        vdf['pb'] / vdf['ind_pb_median'],
        1.0
    )
    vdf['pb_score'] = np.clip((1 - vdf['pb_factor']) * 10, 0, 10)

    vdf['ev_w'] = 0.50
    vdf['fcf_w'] = 0.333
    vdf['pb_w'] = 0.167
    fin_mask = vdf['ind_grp'].isin(['金融', '银行', '保险'])
    vdf.loc[fin_mask, 'ev_w'] = 0.0
    vdf.loc[fin_mask, 'pb_w'] = 0.667
    tech_mask = vdf['ind_grp'] == '科技'
    vdf.loc[tech_mask, 'ev_w'] = 0.617
    vdf.loc[tech_mask, 'pb_w'] = 0.05

    vdf['v_score'] = ((vdf['evoe_score'] * vdf['ev_w'] + vdf['fcf_score'] * vdf['fcf_w'] + vdf['pb_score'] * vdf['pb_w']) * 3.0).round(1)
    v_lookup = dict(zip(vdf['code'].astype(str).str.zfill(6), vdf['v_score'].round(1)))

    valid_mcap = set(
        vdf[(vdf['market_cap'] >= 3e10) | (vdf['total_assets'] >= 1e11)]['code']
        .astype(str).str.zfill(6).tolist()
    )
    now_ts = time.time()
    three_years_sec = 3 * 365 * 24 * 3600
    stock_df['listing_parsed'] = pd.to_datetime(stock_df['listing_date'], format='mixed', errors='coerce')
    stock_df['listing_sec'] = stock_df['listing_parsed'].astype('int64') // 10**9
    valid_listed = set(
        stock_df[stock_df['listing_sec'].notna()]
        [stock_df['listing_sec'] < now_ts - three_years_sec]
        ['code'].astype(str).str.zfill(6).tolist()
    )
    valid_codes = valid_mcap & valid_listed

    results = []
    for code, f in factors.items():
        if code in blacklist:
            continue
        if f.get('q_weighted', 0) <= 0:
            continue
        if code not in valid_codes:
            continue
        q = float(f.get('q_weighted', 0))
        h = float(f.get('h_weighted', 0))
        g = float(f.get('g_weighted', 0))
        v = float(v_lookup.get(code, 15))

        roe_score = float(f.get("quality", {}).get("roe", 0))
        roe_decimal = roe_score / 10 * 0.30
        if roe_decimal < 0.12:
            continue
        q_adj = min(roe_decimal / 0.20, 1.0) if roe_decimal > 0 else 0
        v = round(v * q_adj, 1)
        total = round(q + v + h + g, 1)

        results.append({
            "code": code,
            "name": f.get("name", ""),
            "total": total,
            "q_score": q,
            "v_score": v,
            "h_score": h,
            "g_score": g,
            "roe_pct": round(float(f.get("quality", {}).get("roe", 0)) * 3.0, 1),
        })

    results.sort(key=lambda x: x['total'], reverse=True)
    return results[:50]


def build_score_data(codes: list[str]) -> dict:
    score_data = {}
    for code in codes:
        try:
            result = score_stock(code)
            data = fetch_financials(code, years=5)
            beneish = compute_beneish_m_score(data)
            result["beneish"] = beneish
            result["scores"]["beneish"] = beneish
            score_data[code] = result
        except Exception:
            pass
    return score_data


screener = build_screener_data()
preselect_codes = [s["code"] for s in screener[:30]]
score_data = build_score_data(preselect_codes)

cache = json.loads(CACHE_PATH.read_text())
health = {
    "status": "ok",
    "stocks": cache.get("stocks_count", 0),
    "built_at": cache.get("built_at", ""),
}
search_index = []
for code, f in cache.get("factors", {}).items():
    search_index.append({
        "code": code,
        "name": f.get("name", ""),
        "industry": f.get("industry", ""),
    })

# Build industry summary (simplified from api_server.py /api/industry-summary)
conn = sqlite3.connect(str(DB_PATH))
quotes_df = pd.read_sql(
    "SELECT code, close, pb FROM quotes WHERE trade_date=(SELECT MAX(trade_date) FROM quotes)", conn
)
stock_df = pd.read_sql("SELECT code, total_shares, industry_sw, listing_date FROM stock_info", conn)
income_df = pd.read_sql("SELECT code, operating_profit, interest_expense FROM income WHERE report_period='20251231'", conn)
balance_df = pd.read_sql("SELECT code, total_assets, total_liabilities, equity, cash_equiv, long_term_invest FROM balance WHERE report_period='20251231'", conn)
cf_df = pd.read_sql("SELECT code, operating_cf, capex FROM cashflow WHERE report_period='20251231'", conn)
conn.close()

vdf = balance_df.merge(cf_df, on='code', how='left').merge(quotes_df, on='code', how='left')
vdf = vdf.merge(stock_df, on='code', how='left').merge(income_df, on='code', how='left')
for c in ['close', 'pb', 'total_shares', 'operating_cf', 'capex', 'total_assets',
          'total_liabilities', 'equity', 'cash_equiv', 'long_term_invest',
          'operating_profit', 'interest_expense']:
    if c in vdf.columns:
        vdf[c] = pd.to_numeric(vdf[c], errors='coerce').fillna(0)
vdf['market_cap'] = vdf['close'] * vdf['total_shares']
vdf['ev'] = np.where((vdf['market_cap']>0)&(vdf['total_assets']>1e6), vdf['market_cap']+vdf['total_liabilities']-vdf['cash_equiv']-vdf['long_term_invest']*0.5, np.nan)
vdf['op_earnings'] = vdf['operating_profit'] + vdf['interest_expense'].abs()
vdf['ev_oe'] = np.where((vdf['ev'].notna())&(vdf['ev']>1e6)&(vdf['op_earnings']>1e6), vdf['ev']/vdf['op_earnings'], 999)
vdf['evoe_score'] = np.where(vdf['ev_oe']<999, np.clip((15-vdf['ev_oe'].clip(upper=15))/15*10, 0, 10), 0)
fcf = vdf['operating_cf'] - vdf['capex'].abs()
vdf['fcf_yield_raw'] = np.where(vdf['market_cap']>1e6, fcf/vdf['market_cap'], 0)
vdf['fcf_yield'] = vdf['fcf_yield_raw'].clip(0, 0.15)
vdf['fcf_score'] = np.clip(vdf['fcf_yield']/0.15*10, 0, 10)
vdf['ind_grp'] = vdf['industry_sw'].apply(lambda x: get_industry_group(x) if x else '其他')
ind_pb = vdf[(vdf['pb']>0)&(vdf['pb']<100)].copy()
ind_pb['ind_grp'] = ind_pb['industry_sw'].apply(lambda x: get_industry_group(x) if x else '其他')
ind_medians = ind_pb.groupby('ind_grp')['pb'].median().to_dict()
vdf['ind_pb_median'] = vdf['ind_grp'].map(ind_medians).fillna(2.0)
vdf['pb_factor'] = np.where((vdf['pb']>0)&(vdf['ind_pb_median']>0), vdf['pb']/vdf['ind_pb_median'], 1.0)
vdf['pb_score'] = np.clip((1-vdf['pb_factor'])*10, 0, 10)
vdf['ev_w'] = 0.50; vdf['fcf_w'] = 0.333; vdf['pb_w'] = 0.167
fin_mask = vdf['ind_grp'].isin(['金融', '银行', '保险'])
vdf.loc[fin_mask, 'ev_w'] = 0.0; vdf.loc[fin_mask, 'pb_w'] = 0.667
tech_mask = vdf['ind_grp'] == '科技'
vdf.loc[tech_mask, 'ev_w'] = 0.617; vdf.loc[tech_mask, 'pb_w'] = 0.05
vdf['v_score'] = ((vdf['evoe_score']*vdf['ev_w']+vdf['fcf_score']*vdf['fcf_w']+vdf['pb_score']*vdf['pb_w'])*3.0).round(1)
v_lookup = dict(zip(vdf['code'].astype(str).str.zfill(6), vdf['v_score'].round(1)))

now_ts = time.time()
three_y_sec = 3*365*24*3600
stock_df['listing_parsed'] = pd.to_datetime(stock_df['listing_date'], format='mixed', errors='coerce')
stock_df['listing_sec'] = stock_df['listing_parsed'].astype('int64')//10**9
valid_listed = set(stock_df[stock_df['listing_sec'].notna()&(stock_df['listing_sec']<now_ts-three_y_sec)]['code'].astype(str).str.zfill(6).tolist())
valid_mcap = set(vdf[(vdf['market_cap']>=3e10)|(vdf['total_assets']>=1e11)]['code'].astype(str).str.zfill(6).tolist())
valid_codes = valid_mcap & valid_listed

factors = cache.get("factors", {})
blacklist = cache.get("blacklist", {})
rows = []
for code, f in factors.items():
    if code in blacklist:
        continue
    if code not in valid_codes:
        continue
    roe_score = float(f.get("quality", {}).get("roe", 0))
    if roe_score <= 0:
        continue
    roe_pct = roe_score/10*0.30
    if roe_pct < 0.12:
        continue
    q = float(f.get('q_weighted', 0))
    h = float(f.get('h_weighted', 0))
    g = float(f.get('g_weighted', 0))
    v = float(v_lookup.get(code, 15))
    q_adj = min(roe_pct/0.20, 1.0) if roe_pct>0 else 0
    v = round(v*q_adj, 1)
    total = round(q+v+h+g, 1)
    ind = get_industry_group(f.get('industry', ''))
    rows.append({
        'code': code, 'name': f.get('name',''),
        'total': total, 'q': q, 'v': v, 'h': h, 'g': g,
        'industry': ind, 'roe_pct': round(roe_pct*100, 1)
    })

df_all = pd.DataFrame(rows)
overall = {
    "total_qualified": len(df_all),
    "avg_score": round(df_all['total'].mean(), 1) if len(df_all) else 0,
    "median_score": round(df_all['total'].median(), 1) if len(df_all) else 0,
    "max_score": round(df_all['total'].max(), 1) if len(df_all) else 0,
    "stocks_above_60": int((df_all['total']>=60).sum()) if len(df_all) else 0,
    "stocks_above_50": int((df_all['total']>=50).sum()) if len(df_all) else 0,
}
industries = []
for grp in ['消费', '科技', '医药', '金融', '银行', '保险', '周期', '制造', '公用事业', '地产', '其他']:
    sub = df_all[df_all['industry']==grp]
    if len(sub)==0:
        continue
    top = sub.nlargest(1, 'total').iloc[0]
    top10 = sub.nlargest(10, 'total')[['code','name','total','q','v','h','g']].to_dict('records')
    industries.append({
        "group": grp, "count": len(sub),
        "avg_total": round(sub['total'].mean(), 1),
        "avg_q": round(sub['q'].mean(), 1), "avg_v": round(sub['v'].mean(), 1),
        "avg_h": round(sub['h'].mean(), 1), "avg_g": round(sub['g'].mean(), 1),
        "avg_roe": round(sub['roe_pct'].mean(), 1),
        "top_code": top['code'], "top_name": top['name'],
        "top_score": round(top['total'], 1), "top_10": top10,
    })
industries.sort(key=lambda x: x['avg_total'], reverse=True)

industry_summary = {"overall": overall, "industries": industries}

# Build the _BQAS data blob
data = {
    "health": health,
    "screener": screener,
    "score_data": score_data,
    "search_index": search_index,
    "industry_summary": industry_summary,
}

injection = f"""<script>
window._BQAS = {json.dumps(data, ensure_ascii=False)};
(function(){{
    var _fetch = window.fetch;
    var D = window._BQAS;
    window.fetch = function(url, opts) {{
        var u = String(url);
        if (u.indexOf('/api/health') >= 0)
            return Promise.resolve({{json:function(){{return Promise.resolve(D.health||{{status:'ok'}});}}}});
        if (u.indexOf('/api/screener') >= 0 || u.indexOf('/api/screener-global') >= 0)
            return Promise.resolve({{json:function(){{return Promise.resolve(D.screener||[]);}}}});
        if (u.indexOf('/api/industry-summary') >= 0 || u.indexOf('/api/industry-summary-global') >= 0)
            return Promise.resolve({{json:function(){{return Promise.resolve(D.industry_summary||[]);}}}});
        if (u.indexOf('/api/search') >= 0) {{
            var q = (new URL(u,'http://x').searchParams.get('q')||'').toLowerCase();
            var results = (D.search_index||[]).filter(function(s){{
                return s.code.indexOf(q)>=0 || s.name.toLowerCase().indexOf(q)>=0 || s.industry.indexOf(q)>=0;
            }});
            return Promise.resolve({{json:function(){{return Promise.resolve(results);}}}});
        }}
        if (u.indexOf('/api/compare') >= 0) {{
            var codes = (u.split('codes=')[1]||'').split('&')[0].split(',');
            var results = (D.compare||[]).filter(function(s){{return codes.indexOf(s.code)>=0;}});
            return Promise.resolve({{json:function(){{return Promise.resolve(results);}}}});
        }}
        if (u.indexOf('/api/score/') >= 0 || u.indexOf('/api/score-global/') >= 0) {{
            var code = u.split('/').pop().split('?')[0];
            var full = (D.score_data||{{}})[code];
            console.log('BQAS fetch intercepted: /api/score/'+code, full?'found':'not found');
            return Promise.resolve({{json:function(){{return Promise.resolve(full||{{error:'未找到该股票数据'}});}}}});
        }}
        if (u.indexOf('/api/stock-profile/') >= 0) {{
            var code = u.split('/').pop();
            var full = (D.score_data||{{}})[code];
            return Promise.resolve({{json:function(){{return Promise.resolve(full||{{}});}}}});
        }}
        return _fetch.call(window, url, opts);
    }};
}})();
</script>
"""

html = html_template.replace('</head>', injection + '\n</head>')
st.components.v1.html(html, height=2400, scrolling=True)
