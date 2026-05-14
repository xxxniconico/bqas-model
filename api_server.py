#!/usr/bin/env python3
"""BQAS API Server — Flask backend for interactive frontend."""
import sys, json, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np

from flask import Flask, request, jsonify
from flask_cors import CORS

from bqas.data.fetcher import fetch_financials, _get_conn
from bqas.engine.scorer import score_stock
from bqas.engine.scorer_global import score_stock_global
from bqas.engine.beneish import compute_beneish_m_score

app = Flask(__name__)
CORS(app)

DB_PATH = Path(__file__).parent / "bqas" / "data" / "cache" / "bqas.db"


@app.route("/api/score/<code>")
def api_score(code):
    """Score a single stock with full breakdown."""
    try:
        result = score_stock(code)
        # Add Beneish M-Score
        data = fetch_financials(code, years=5)
        beneish = compute_beneish_m_score(data)
        result["beneish"] = beneish
        result["scores"]["beneish"] = beneish
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "code": code}), 500


@app.route("/api/score-global/<code>")
def api_score_global(code):
    """Score a US/HK stock. Query param: ?market=us or ?market=hk"""
    market = request.args.get("market", "us")
    try:
        result = score_stock_global(code, market)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "code": code}), 500


@app.route("/api/compare")
def api_compare():
    """Compare multiple stocks."""
    codes = request.args.get("codes", "").split(",")
    codes = [c.strip() for c in codes if c.strip()]
    if not codes:
        return jsonify({"error": "No codes provided"}), 400
    
    results = []
    for code in codes[:5]:  # max 5
        try:
            r = score_stock(code)
            results.append(r)
        except Exception as e:
            results.append({"code": code, "error": str(e)})
    return jsonify(results)


@app.route("/api/screener")
def api_screener():
    """Stock screener — factor cache (Q/H/G) + real-time V dimension.

    Auto-builds cache if missing. No fallback — always uses real model formulas.
    """
    try:
        from pathlib import Path as _Path
        import json as _json

        cache_path = _Path(__file__).parent / "bqas" / "data" / "cache" / "factor_cache.json"

        if cache_path.exists():
            cache = _json.loads(cache_path.read_text())
        else:
            # Auto-build cache if missing
            from bqas.engine.cache_layer import build_factor_cache
            build_factor_cache(force=True)
            cache = _json.loads(cache_path.read_text())

        factors = cache.get("factors", {})
        blacklist = cache.get("blacklist", {})

        if not factors:
            return jsonify({"error": "Factor cache empty after rebuild"}), 500

        # ── Load data for V dimension computation (CLI-aligned formulas) ──
        import sqlite3 as _sql
        db_path = str(_Path(__file__).parent / "bqas" / "data" / "cache" / "bqas.db")
        conn = _sql.connect(db_path)

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

        # Merge
        vdf = balance_df.merge(cf_df, on='code', how='left').merge(quotes_df, on='code', how='left')
        vdf = vdf.merge(stock_df, on='code', how='left').merge(income_df, on='code', how='left')
        for c in ['close', 'pb', 'total_shares', 'operating_cf', 'capex', 'total_assets',
                  'total_liabilities', 'equity', 'cash_equiv', 'long_term_invest',
                  'operating_profit', 'interest_expense']:
            if c in vdf.columns:
                vdf[c] = pd.to_numeric(vdf[c], errors='coerce').fillna(0)

        # Market cap
        vdf['market_cap'] = vdf['close'] * vdf['total_shares']

        # ══ CLI-aligned V dimension formulas ══

        # EV = mcap + total_liabilities - cash - long_term_invest * 0.5
        vdf['ev'] = np.where(
            (vdf['market_cap'] > 0) & (vdf['total_assets'] > 1e6),
            vdf['market_cap'] + vdf['total_liabilities'] - vdf['cash_equiv'] - vdf['long_term_invest'] * 0.5,
            np.nan
        )

        # EV/OE: op_earnings = operating_profit + interest_expense
        vdf['op_earnings'] = vdf['operating_profit'] + vdf['interest_expense'].abs()
        vdf['ev_oe'] = np.where(
            (vdf['ev'].notna()) & (vdf['ev'] > 1e6) & (vdf['op_earnings'] > 1e6),
            vdf['ev'] / vdf['op_earnings'],
            999
        )
        # CLI: score = max(0, (15 - min(EV/OE, 15)) / 15 * 10)
        vdf['evoe_score'] = np.where(
            vdf['ev_oe'] < 999,
            np.clip((15 - vdf['ev_oe'].clip(upper=15)) / 15 * 10, 0, 10),
            0
        )

        # FCF Yield: FCF = OCF - capex, yield = FCF / market_cap (NOT /EV!)
        fcf = vdf['operating_cf'] - vdf['capex'].abs()
        vdf['fcf_yield_raw'] = np.where(vdf['market_cap'] > 1e6, fcf / vdf['market_cap'], 0)
        vdf['fcf_yield'] = vdf['fcf_yield_raw'].clip(0, 0.15)
        vdf['fcf_score'] = np.clip(vdf['fcf_yield'] / 0.15 * 10, 0, 10)

        # PB industry adjustment (CLI: score = max(0, 1 - pb/ind_median) * 10)
        from bqas.engine.industry import get_industry_group
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

        # ══ V sub-factor weights with industry special rules ══
        # Default: EV=0.50 FCF=0.333 PB=0.167 (match CLI: 15/10/5/30 weights)
        vdf['ev_w'] = 0.50
        vdf['fcf_w'] = 0.333
        vdf['pb_w'] = 0.167

        # Financial (banks/insurance/securities): PB replaces EV entirely (PB=0.667, EV=0)
        fin_mask = vdf['ind_grp'].isin(['金融', '银行', '保险'])
        vdf.loc[fin_mask, 'ev_w'] = 0.0
        vdf.loc[fin_mask, 'pb_w'] = 0.667

        # Tech: PB weight reduced to 30% of original (PB=0.05, EV absorbs freed weight)
        tech_mask = vdf['ind_grp'] == '科技'
        vdf.loc[tech_mask, 'ev_w'] = 0.617  # 0.185/0.30
        vdf.loc[tech_mask, 'pb_w'] = 0.05   # 0.015/0.30

        vdf['v_score'] = ((vdf['evoe_score'] * vdf['ev_w'] + vdf['fcf_score'] * vdf['fcf_w'] + vdf['pb_score'] * vdf['pb_w']) * 3.0).round(1)

        # Include ALL stocks (even v_score=0) — fallback 15 only for truly missing data
        v_lookup = dict(zip(
            vdf['code'].astype(str).str.zfill(6),
            vdf['v_score'].round(1)
        ))

        # ══ A+D: 质量护栏 (market cap / total_assets + listing years) ══
        import time as _time
        valid_mcap = set(
            vdf[(vdf['market_cap'] >= 3e10) | (vdf['total_assets'] >= 1e11)]['code']
            .astype(str).str.zfill(6).tolist()
        )
        now_ts = _time.time()
        three_years_sec = 3 * 365 * 24 * 3600
        # listing_date is 'YYYY-MM-DD' string from cache build
        stock_df['listing_parsed'] = pd.to_datetime(
            stock_df['listing_date'], format='mixed', errors='coerce'
        )
        stock_df['listing_sec'] = stock_df['listing_parsed'].astype('int64') // 10**9
        valid_listed = set(
            stock_df[stock_df['listing_sec'].notna()]
            [stock_df['listing_sec'] < now_ts - three_years_sec]
            ['code'].astype(str).str.zfill(6).tolist()
        )
        valid_codes = valid_mcap & valid_listed

        # Build ranking
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
            v = float(v_lookup.get(code, 15))  # 15 fallback ONLY for stocks missing from quotes entirely

            # 质量调整估值: V × min(ROE_3y / 20%, 1)
            roe_score = float(f.get("quality", {}).get("roe", 0))
            roe_decimal = roe_score / 10 * 0.30  # 反向换算: score→ROE%
            # B: ROE floor < 12% → exclude
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
        return jsonify(results[:50])

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/industry-summary")
def api_industry_summary():
    """A-share Buffett-model index — overall stats + per-industry breakdown.

    Uses factor cache (Q/H/G) + real-time V from screener pipeline.
    """
    try:
        from pathlib import Path as _Path
        import json as _json

        cache_path = _Path(__file__).parent / "bqas" / "data" / "cache" / "factor_cache.json"
        if not cache_path.exists():
            return jsonify({"error": "Factor cache not built yet"}), 503

        cache = _json.loads(cache_path.read_text())
        factors = cache.get("factors", {})
        blacklist = cache.get("blacklist", {})

        # Quick V scores (same pipeline as screener, simplified)
        import sqlite3 as _sql
        db_path = str(_Path(__file__).parent / "bqas" / "data" / "cache" / "bqas.db")
        conn = _sql.connect(db_path)
        quotes_df = pd.read_sql(
            "SELECT code, close, pb FROM quotes WHERE trade_date=(SELECT MAX(trade_date) FROM quotes)",
            conn
        )
        stock_df = pd.read_sql(
            "SELECT code, total_shares, industry_sw, listing_date FROM stock_info", conn
        )
        income_df = pd.read_sql(
            "SELECT code, operating_profit, interest_expense FROM income WHERE report_period='20251231'",
            conn
        )
        balance_df = pd.read_sql(
            "SELECT code, total_assets, total_liabilities, equity, cash_equiv, long_term_invest FROM balance WHERE report_period='20251231'",
            conn
        )
        cf_df = pd.read_sql(
            "SELECT code, operating_cf, capex FROM cashflow WHERE report_period='20251231'",
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
            vdf['ev'] / vdf['op_earnings'], 999
        )
        vdf['evoe_score'] = np.where(
            vdf['ev_oe'] < 999,
            np.clip((15 - vdf['ev_oe'].clip(upper=15)) / 15 * 10, 0, 10), 0
        )
        fcf = vdf['operating_cf'] - vdf['capex'].abs()
        vdf['fcf_yield_raw'] = np.where(vdf['market_cap'] > 1e6, fcf / vdf['market_cap'], 0)
        vdf['fcf_yield'] = vdf['fcf_yield_raw'].clip(0, 0.15)
        vdf['fcf_score'] = np.clip(vdf['fcf_yield'] / 0.15 * 10, 0, 10)

        from bqas.engine.industry import get_industry_group
        vdf['ind_grp'] = vdf['industry_sw'].apply(lambda x: get_industry_group(x) if x else '其他')
        # PB by industry
        ind_pb = vdf[(vdf['pb'] > 0) & (vdf['pb'] < 100)].copy()
        ind_pb['ind_grp'] = ind_pb['industry_sw'].apply(lambda x: get_industry_group(x) if x else '其他')
        ind_medians = ind_pb.groupby('ind_grp')['pb'].median().to_dict()
        vdf['ind_pb_median'] = vdf['ind_grp'].map(ind_medians).fillna(2.0)
        vdf['pb_factor'] = np.where((vdf['pb']>0)&(vdf['ind_pb_median']>0), vdf['pb']/vdf['ind_pb_median'], 1.0)
        vdf['pb_score'] = np.clip((1 - vdf['pb_factor']) * 10, 0, 10)

        # Industry-adjusted V weights
        vdf['ev_w'] = 0.50; vdf['fcf_w'] = 0.333; vdf['pb_w'] = 0.167
        fin_mask = vdf['ind_grp'].isin(['金融', '银行', '保险'])
        vdf.loc[fin_mask, 'ev_w'] = 0.0; vdf.loc[fin_mask, 'pb_w'] = 0.667
        tech_mask = vdf['ind_grp'] == '科技'
        vdf.loc[tech_mask, 'ev_w'] = 0.617; vdf.loc[tech_mask, 'pb_w'] = 0.05

        vdf['v_score'] = ((vdf['evoe_score']*vdf['ev_w'] + vdf['fcf_score']*vdf['fcf_w'] + vdf['pb_score']*vdf['pb_w']) * 3.0).round(1)
        v_lookup = dict(zip(vdf['code'].astype(str).str.zfill(6), vdf['v_score'].round(1)))

        # A+D filters (market cap + listing years) — match screener
        import time as _t
        now_ts = _t.time()
        three_y_sec = 3 * 365 * 24 * 3600
        stock_df['listing_parsed'] = pd.to_datetime(stock_df['listing_date'], format='mixed', errors='coerce')
        stock_df['listing_sec'] = stock_df['listing_parsed'].astype('int64') // 10**9
        valid_listed = set(
            stock_df[stock_df['listing_sec'].notna() & (stock_df['listing_sec'] < now_ts - three_y_sec)]
            ['code'].astype(str).str.zfill(6).tolist()
        )
        # A: market_cap >= 30B OR total_assets >= 100B
        valid_mcap = set(
            vdf[(vdf['market_cap'] >= 3e10) | (vdf['total_assets'] >= 1e11)]
            ['code'].astype(str).str.zfill(6).tolist()
        )
        valid_codes = valid_mcap & valid_listed

        # Build per-stock scores
        rows = []
        for code, f in factors.items():
            if code in blacklist:
                continue
            # A+D: market cap + listing years
            if code not in valid_codes:
                continue
            roe_score = float(f.get("quality", {}).get("roe", 0))
            if roe_score <= 0:
                continue
            roe_pct = roe_score / 10 * 0.30
            if roe_pct < 0.12:  # B: ROE floor
                continue

            q = float(f.get('q_weighted', 0))
            h = float(f.get('h_weighted', 0))
            g = float(f.get('g_weighted', 0))
            v = float(v_lookup.get(code, 15))
            q_adj = min(roe_pct / 0.20, 1.0) if roe_pct > 0 else 0
            v = round(v * q_adj, 1)
            total = round(q + v + h + g, 1)
            ind = get_industry_group(f.get('industry', ''))

            rows.append({
                'code': code, 'name': f.get('name', ''),
                'total': total, 'q': q, 'v': v, 'h': h, 'g': g,
                'industry': ind, 'roe_pct': round(roe_pct * 100, 1)
            })

        if not rows:
            return jsonify({"error": "No qualified stocks"}), 500

        df_all = pd.DataFrame(rows)

        # Overall stats
        overall = {
            "total_qualified": len(df_all),
            "avg_score": round(df_all['total'].mean(), 1),
            "median_score": round(df_all['total'].median(), 1),
            "max_score": round(df_all['total'].max(), 1),
            "stocks_above_60": int((df_all['total'] >= 60).sum()),
            "stocks_above_50": int((df_all['total'] >= 50).sum()),
        }

        # Per-industry breakdown
        industries = []
        for grp in ['消费', '科技', '医药', '金融', '银行', '保险', '周期', '制造', '公用事业', '地产', '其他']:
            sub = df_all[df_all['industry'] == grp]
            if len(sub) == 0:
                continue
            top = sub.nlargest(1, 'total').iloc[0]
            top10 = sub.nlargest(10, 'total')[['code', 'name', 'total', 'q', 'v', 'h', 'g']].to_dict('records')
            industries.append({
                "group": grp,
                "count": len(sub),
                "avg_total": round(sub['total'].mean(), 1),
                "avg_q": round(sub['q'].mean(), 1),
                "avg_v": round(sub['v'].mean(), 1),
                "avg_h": round(sub['h'].mean(), 1),
                "avg_g": round(sub['g'].mean(), 1),
                "avg_roe": round(sub['roe_pct'].mean(), 1),
                "top_code": top['code'],
                "top_name": top['name'],
                "top_score": round(top['total'], 1),
                "top_10": top10,
            })
        industries.sort(key=lambda x: x['avg_total'], reverse=True)

        return jsonify({
            "overall": overall,
            "industries": industries,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stock-profile/<code>")
def api_stock_profile(code):
    """52-week price stats + company overview. Auto-detects market."""
    try:
        import subprocess as _sp, json as _json, re as _re, sqlite3 as _sql
        from pathlib import Path as _Path

        # Detect market
        if ".HK" in code.upper():
            market = "hk"
        elif code.isalpha() and not code[0].isdigit():
            market = "us"
        else:
            market = "cn"

        # ── US/HK: use yfinance ──
        if market in ("us", "hk"):
            import yfinance as yf
            ticker = yf.Ticker(code)
            info = ticker.info or {}
            hist = ticker.history(period="1y")
            kline_stats = {}
            if not hist.empty:
                closes = hist['Close'].tolist()
                latest = closes[-1]
                high_52w = max(closes)
                low_52w = min(closes)
                yr_ago = closes[0] if len(closes) > 0 else latest
                change_52w = (latest - yr_ago) / yr_ago * 100 if yr_ago > 0 else 0
                month_idx = max(0, len(closes) - 22)
                month_ago = closes[month_idx]
                change_1m = (latest - month_ago) / month_ago * 100 if month_ago > 0 else 0
                if len(closes) >= 20:
                    import statistics as _st
                    recent = closes[-20:]
                    m = _st.mean(recent)
                    vol = (_st.stdev(recent) / m * 100) if m > 0 else 0
                else:
                    vol = 0
                kline_stats = {
                    "latest_price": round(latest, 2), "high_52w": round(high_52w, 2),
                    "low_52w": round(low_52w, 2), "change_52w_pct": round(change_52w, 1),
                    "change_1m_pct": round(change_1m, 1), "volatility_20d_pct": round(vol, 1),
                }
            company = {
                "full_name": info.get('longName', ''),
                "industry": info.get('industry', ''),
                "sector": info.get('sector', ''),
                "country": info.get('country', ''),
                "website": info.get('website', ''),
                "employees": str(info.get('fullTimeEmployees', '')),
                "description": (info.get('longBusinessSummary', '') or '')[:500],
                "market_cap_e8": round(info.get('marketCap', 0) / 1e8, 1),
            }
            return jsonify({"code": code, "market": market, "company": company, "kline_stats": kline_stats})

        # ── A-share: Sina ──
        prefix = "sh" if code.startswith(("60", "68")) else "sz"
        symbol = f"{prefix}{code}"
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
               f"CN_MarketDataService.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=260")
        result = _sp.run(
            f'curl -s --max-time 10 "{url}" -H "Referer: https://finance.sina.com.cn"',
            shell=True, capture_output=True, text=True, timeout=12
        )
        raw = result.stdout

        kline_stats = {}
        if 'data(' in raw:
            start = raw.index('data(') + 5
            end = raw.rindex(')')
            klines = _json.loads(raw[start:end])
            if klines and len(klines) > 0:
                closes = [float(k['close']) for k in klines if k.get('close')]
                if closes:
                    latest = closes[-1]
                    high_52w = max(closes[-250:]) if len(closes) > 250 else max(closes)
                    low_52w = min(closes[-250:]) if len(closes) > 250 else min(closes)
                    # Price 1 year ago (~250 trading days)
                    yr_ago_idx = max(0, len(closes) - 250)
                    yr_ago_price = closes[yr_ago_idx] if yr_ago_idx < len(closes) else closes[0]
                    change_52w = (latest - yr_ago_price) / yr_ago_price * 100 if yr_ago_price > 0 else 0
                    # Recent 1-month change
                    month_ago_idx = max(0, len(closes) - 22)
                    month_ago_price = closes[month_ago_idx]
                    change_1m = (latest - month_ago_price) / month_ago_price * 100 if month_ago_price > 0 else 0
                    # Volatility (20-day)
                    if len(closes) >= 20:
                        import statistics as _st
                        recent_20 = closes[-20:]
                        mean_20 = _st.mean(recent_20)
                        vol_20d = (_st.stdev(recent_20) / mean_20 * 100) if mean_20 > 0 else 0
                    else:
                        vol_20d = 0

                    kline_stats = {
                        "latest_price": round(latest, 2),
                        "high_52w": round(high_52w, 2),
                        "low_52w": round(low_52w, 2),
                        "change_52w_pct": round(change_52w, 1),
                        "change_1m_pct": round(change_1m, 1),
                        "volatility_20d_pct": round(vol_20d, 1),
                        "trading_days": len(klines),
                    }

        # ── 2. Company info from Sina Corp page ──
        corp_url = f"http://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/{code}.phtml"
        result2 = _sp.run(
            ["curl", "-sL", "--max-time", "8", corp_url,
             "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, timeout=10
        )
        html = result2.stdout.decode("gbk", errors="replace")

        company = {}
        # ── 公司简介 & 主营业务（从HTML注释中提取）──
        m_desc = _re.search(r'公司简介[：:]\s*(.+?)(?:公司简介end|主营业务)', html, _re.DOTALL)
        if m_desc:
            desc = _re.sub(r'<[^>]+>', '', m_desc.group(1)).strip()
            desc = desc.replace('&nbsp;', '').replace('\r', '').replace('\t', '')
            if desc and len(desc) > 10:
                company["description"] = desc[:400]

        m_biz = _re.search(r'主营业务[：:]\s*(.+?)(?:公司简介end|<br|↑)', html, _re.DOTALL)
        if m_biz:
            biz = _re.sub(r'<[^>]+>', '', m_biz.group(1)).strip()
            biz = biz.replace('&nbsp;', '').replace('\r', '').replace('\t', '')
            if biz and len(biz) > 2:
                company["main_business"] = biz[:300]

        # Extract key fields
        for field, pattern in [
            ("full_name", r'公司名称[：:]\s*(?:</[^>]*>)*\s*([^<\n]+)'),
            ("english_name", r'英文名称[：:]\s*(?:</[^>]*>)*\s*([^<\n]+)'),
            ("registered_capital", r'注册资本[：:]\s*(?:</[^>]*>)*\s*([^<\n]+)'),
            ("chairman", r'董事长[：:]\s*(?:</[^>]*>)*\s*([^<\n]+)'),
            ("website", r'公司网址[：:]\s*(?:</[^>]*>)*\s*([^<\s]+)'),
            ("employees", r'员工人数[：:]\s*(?:</[^>]*>)*\s*([^<\n]+)'),
        ]:
            m = _re.search(pattern, html)
            if m:
                val = m.group(1).strip()
                if val and val not in ("—", "-", "", "暂无"):
                    company[field] = val

        # Industry from stock_info
        db_path = str(_Path(__file__).parent / "bqas" / "data" / "cache" / "bqas.db")
        conn = _sql.connect(db_path)
        conn.row_factory = _sql.Row
        row = conn.execute("SELECT name, industry_sw, listing_date, total_shares FROM stock_info WHERE code=?", (code,)).fetchone()
        conn.close()

        if row:
            company["name"] = row["name"] or code
            company["industry"] = row["industry_sw"] or ""
            if row["listing_date"]:
                company["listing_date"] = str(row["listing_date"])
            if row["total_shares"] and row["total_shares"] > 0:
                company["total_shares_e8"] = round(row["total_shares"] / 1e8, 2)

        return jsonify({
            "code": code,
            "company": company,
            "kline_stats": kline_stats,
        })
    except Exception as e:
        return jsonify({"error": str(e), "code": code}), 500


@app.route("/api/screener-global")
def api_screener_global():
    """US/HK stock screener. Query: ?market=us or ?market=hk"""
    market = request.args.get("market", "us")
    try:
        import sqlite3 as _sql
        from pathlib import Path as _Path
        db_path = str(_Path(__file__).parent / "bqas" / "data" / "cache" / "bqas_global.db")
        conn = _sql.connect(db_path)
        conn.row_factory = _sql.Row

        rows = conn.execute(
            f"SELECT code, name FROM stock_info_{market} ORDER BY name"
        ).fetchall()

        results = []
        for row in rows:
            try:
                r = score_stock_global(row["code"], market)
                if r.get("total", 0) > 0:
                    w = r.get("scores", {}).get("weighted", {})
                    results.append({
                        "code": row["code"], "name": r.get("name", ""),
                        "total": r["total"],
                        "q_score": w.get("quality", 0), "v_score": w.get("value", 0),
                        "h_score": w.get("health", 0), "g_score": w.get("gov", 0),
                        "roe_pct": 0,
                    })
            except Exception:
                continue

        conn.close()
        results.sort(key=lambda x: x['total'], reverse=True)
        return jsonify(results[:30])

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/industry-summary-global")
def api_industry_summary_global():
    """US/HK market index — industry breakdown. Query: ?market=us or ?market=hk"""
    market = request.args.get("market", "us")
    try:
        import sqlite3 as _sql
        from pathlib import Path as _Path

        db_path = str(_Path(__file__).parent / "bqas" / "data" / "cache" / "bqas_global.db")
        conn = _sql.connect(db_path)
        conn.row_factory = _sql.Row
        rows = conn.execute(
            f"SELECT code, name, industry, sector, market_cap FROM stock_info_{market}"
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            try:
                r = score_stock_global(row["code"], market)
                if r.get("total", 0) > 10:
                    w = r.get("scores", {}).get("weighted", {})
                    sector = row["sector"] or row["industry"] or "Other"
                    results.append({
                        "code": row["code"], "name": r.get("name", ""),
                        "total": r["total"], "q": w.get("quality", 0),
                        "v": w.get("value", 0), "h": w.get("health", 0),
                        "g": w.get("gov", 0), "sector": sector,
                    })
            except Exception:
                continue

        if not results:
            return jsonify({"error": "No stocks scored"}), 500

        df = pd.DataFrame(results)
        overall = {
            "total_qualified": len(df),
            "avg_score": round(df['total'].mean(), 1),
            "median_score": round(df['total'].median(), 1),
            "max_score": round(df['total'].max(), 1),
            "stocks_above_60": int((df['total'] >= 60).sum()),
            "stocks_above_50": int((df['total'] >= 50).sum()),
        }

        sectors_dict = {}
        for _, r in df.iterrows():
            sec = r['sector']
            if sec not in sectors_dict:
                sectors_dict[sec] = []
            sectors_dict[sec].append(r)

        industries = []
        for sec, stocks in sorted(sectors_dict.items(), key=lambda x: -len(x[1])):
            sub = pd.DataFrame(stocks)
            top = sub.nlargest(1, 'total').iloc[0]
            top10 = sub.nlargest(10, 'total')[['code', 'name', 'total', 'q', 'v', 'h', 'g']].to_dict('records')
            industries.append({
                "group": sec, "count": len(sub),
                "avg_total": round(sub['total'].mean(), 1),
                "avg_q": round(sub['q'].mean(), 1), "avg_v": round(sub['v'].mean(), 1),
                "avg_h": round(sub['h'].mean(), 1), "avg_g": round(sub['g'].mean(), 1),
                "avg_roe": 0, "top_code": top['code'], "top_name": top['name'],
                "top_score": round(top['total'], 1), "top_10": top10,
            })

        return jsonify({"overall": overall, "industries": industries})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search")
def api_search():
    """Search stocks by code or name across A-shares, HK, and US."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 1:
        return jsonify([])
    
    results = []
    
    # ── A-shares ──
    conn = sqlite3.connect(str(DB_PATH))
    if q.isdigit():
        rows = conn.execute(
            "SELECT code, name, industry_sw FROM stock_info WHERE code LIKE ? LIMIT 10",
            (f"%{q}%",)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT code, name, industry_sw FROM stock_info WHERE name LIKE ? LIMIT 10",
            (f"%{q}%",)
        ).fetchall()
    conn.close()
    for r in rows:
        results.append({"code": r[0], "name": r[1], "industry": r[2], "market": "cn"})
    
    # ── HK & US (global DB) ──
    try:
        global_db = str(Path(__file__).parent / "bqas" / "data" / "cache" / "bqas_global.db")
        gconn = sqlite3.connect(global_db)
        for market in ["hk", "us"]:
            m = market
            if q.isdigit():
                rows = gconn.execute(
                    f"SELECT code, name FROM stock_info_{m} WHERE code LIKE ? LIMIT 10",
                    (f"%{q}%",)
                ).fetchall()
            else:
                rows = gconn.execute(
                    f"SELECT code, name FROM stock_info_{m} WHERE name LIKE ? LIMIT 10",
                    (f"%{q}%",)
                ).fetchall()
            for r in rows:
                results.append({"code": r[0], "name": r[1], "industry": "", "market": m})
        gconn.close()
    except Exception:
        pass  # global DB not available yet
    
    return jsonify(results[:10])


@app.route("/api/quick-score/<code>")
def api_quick_score(code):
    """Quick scoring with key metrics only (faster)."""
    try:
        result = score_stock(code)
        return jsonify({
            "code": result["code"],
            "name": result["name"],
            "total": result["total"],
            "rating": result["rating"],
            "rating_label": result["rating_label"],
            "passed_blacklist": result["passed_blacklist"],
            "blacklist_reason": result.get("blacklist_reason", ""),
            "industry_group": result.get("industry_group", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e), "code": code}), 500


@app.route("/api/health")
def api_health():
    """Health check with cache freshness info."""
    conn = sqlite3.connect(str(DB_PATH))
    stocks = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
    quotes = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    periods = conn.execute("SELECT COUNT(DISTINCT report_period) FROM income").fetchone()[0]
    conn.close()

    # Check factor cache freshness
    cache_info = {}
    cache_path = Path(__file__).parent / "bqas" / "data" / "cache" / "factor_cache.json"
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        cache_info = {
            "cached_stocks": cache.get("stocks_count", 0),
            "built_at": cache.get("built_at", ""),
        }

    return jsonify({
        "status": "ok",
        "stocks": stocks,
        "quotes": quotes,
        "periods": periods,
        "cache": cache_info,
    })


@app.route("/")
def index():
    """Serve the dashboard HTML."""
    html_path = Path(__file__).parent / "dashboard.html"
    return html_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}


if __name__ == "__main__":
    print("🚀 BQAS Dashboard on http://127.0.0.1:8503")
    app.run(host="127.0.0.1", port=8503, debug=False)
