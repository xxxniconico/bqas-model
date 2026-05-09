#!/usr/bin/env python3
"""BQAS API Server — Flask backend for interactive frontend."""
import sys, json, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, request, jsonify
from flask_cors import CORS

from bqas.data.fetcher import fetch_financials, _get_conn
from bqas.engine.scorer import score_stock
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
    """Stock screener — return top stocks with key metrics from batch_rank3 cache."""
    try:
        import subprocess, json as _json
        # Run batch_rank3 and capture JSON output
        result = subprocess.run(
            [sys.executable, "-c", """
import sys; sys.path.insert(0,'.')
import sqlite3, json
import pandas as pd, numpy as np

db = sqlite3.connect('bqas/data/cache/bqas.db')
income = pd.read_sql("SELECT code, revenue, operating_profit, net_income FROM income WHERE report_period='20251231'", db)
balance = pd.read_sql("SELECT code, total_assets, total_liabilities, equity, short_term_debt, long_term_debt, goodwill FROM balance WHERE report_period='20251231'", db)
cashflow = pd.read_sql("SELECT code, operating_cf, capex FROM cashflow WHERE report_period='20251231'", db)
stocks = pd.read_sql("SELECT code, name FROM stock_info", db)
db.close()

df = income.merge(balance, on='code').merge(cashflow, on='code').merge(stocks, on='code', how='left')
for c in df.columns:
    if c not in ('code','name'): df[c] = pd.to_numeric(df[c],errors='coerce').fillna(0)

df = df[(df.equity>1e6)&(df.total_assets>1e6)&(df.revenue>1e6)]

df['roe'] = np.where(df.equity>0, df.net_income/df.equity, 0)
df['roic'] = np.where(df.equity+df.short_term_debt+df.long_term_debt>0, (df.operating_profit+df.net_income.abs()*0)/df.equity, 0)
df['ebit'] = df.operating_profit + df.net_income.abs()*0  # placeholder
df['invested'] = df.equity + df.short_term_debt + df.long_term_debt
df['roic'] = np.where(df.invested>1e6, df.ebit/df.invested, 0)
df['fcf'] = df.operating_cf - df.capex.abs()
df['debt_ratio'] = np.where(df.total_assets>0, df.total_liabilities/df.total_assets, 0)
df['ic'] = np.where(df.net_income.abs()>1e6, (df.operating_profit+df.net_income.abs()*0.2)/df.net_income.abs()*10, 10)

q1 = np.clip(df.roe/0.30,0,1)*12
q2 = np.clip(df.roic/0.30,0,1)*10
v1 = np.clip(df.roic/0.30,0,1)*15
h1 = np.clip(1-df.debt_ratio/0.70,0,1)*8
h2 = np.clip(df.ic/20,0,1)*7

df['total'] = (q1+q2+v1+h1+h2+3.5).round(1)
df['q_score'] = (q1+q2).round(1)
df['v_score'] = v1.round(1)
df['h_score'] = (h1+h2).round(1)
df['g_score'] = 3.5
df['roe_pct'] = (df.roe*100).round(1)

top = df.nlargest(50, 'total')
print(json.dumps(top[['code','name','total','q_score','v_score','h_score','g_score','roe_pct']].to_dict('records')))
"""],
            capture_output=True, text=True, timeout=60, cwd=str(Path(__file__).parent),
        )
        if result.returncode == 0:
            data = _json.loads(result.stdout.strip())
            return jsonify(data)
        return jsonify({"error": result.stderr[:500]}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search")
def api_search():
    """Search stocks by code or name."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 1:
        return jsonify([])
    
    conn = sqlite3.connect(str(DB_PATH))
    if q.isdigit():
        # Search by code
        rows = conn.execute(
            "SELECT code, name, industry_sw FROM stock_info WHERE code LIKE ? LIMIT 10",
            (f"%{q}%",)
        ).fetchall()
    else:
        # Search by name
        rows = conn.execute(
            "SELECT code, name, industry_sw FROM stock_info WHERE name LIKE ? LIMIT 10",
            (f"%{q}%",)
        ).fetchall()
    conn.close()
    
    return jsonify([
        {"code": r[0], "name": r[1], "industry": r[2]}
        for r in rows
    ])


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
    """Health check."""
    conn = sqlite3.connect(str(DB_PATH))
    stocks = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
    quotes = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    periods = conn.execute("SELECT COUNT(DISTINCT report_period) FROM income").fetchone()[0]
    conn.close()
    return jsonify({
        "status": "ok",
        "stocks": stocks,
        "quotes": quotes,
        "periods": periods,
    })


@app.route("/")
def index():
    """Serve the dashboard HTML."""
    html_path = Path(__file__).parent / "dashboard.html"
    return html_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}


if __name__ == "__main__":
    print("🚀 BQAS Dashboard on http://127.0.0.1:8503")
    app.run(host="127.0.0.1", port=8503, debug=False)
