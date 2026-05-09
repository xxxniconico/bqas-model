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


@app.route("/api/top/<int:n>")
def api_top(n):
    """Get top N stocks from batch ranking."""
    n = min(n, 100)
    
    # Run batch_rank3.py and capture output
    import subprocess
    result = subprocess.run(
        [sys.executable, "batch_rank3.py"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent),
        timeout=300,
    )
    
    # Parse the output for top stocks
    # batch_rank3 prints a table, we'll use a different approach:
    # Actually, let's read from the DB directly based on latest scoring
    return jsonify({"message": "Top ranking requires batch_rank3 pre-run", "count": 0})


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


if __name__ == "__main__":
    print("🚀 BQAS API Server starting on http://127.0.0.1:8503")
    app.run(host="127.0.0.1", port=8503, debug=False)
