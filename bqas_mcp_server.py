#!/usr/bin/env python3
"""
Stock MCP Server — A股/港股/美股行情 + 大宗商品 + 外汇 + 宏观ETF.

Zero API key, Sina Finance only. Works from WSL / mainland China.

Tools (7):
  stock_quote    — A/HK/US 股票实时行情
  stock_search   — A股代码/名称搜索
  market_indices — 9大指数
  stock_history  — 美股日K线
  commodities    — 黄金/白银/WTI原油
  forex          — USD/CNY 汇率
  macro_etfs     — VIXY/UUP/TLT/SHY/GLD 宏观情绪指标
"""
import json, subprocess, sys, time, sqlite3
from pathlib import Path

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
DB_PATH = _ROOT / "bqas" / "data" / "cache" / "bqas.db"

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("stock-mcp")

# ═══════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════

def _curl_sina(symbol: str) -> dict | None:
    """Parse Sina quote for stock/index tickers."""
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "8", f"https://hq.sinajs.cn/list={symbol}",
             "-H", "Referer: https://finance.sina.com.cn"],
            capture_output=True, timeout=10)
        raw = result.stdout.decode("gbk")
        if '="' not in raw: return None
        f = raw.split('="')[1].rstrip('";').split(",")
        if len(f) < 10: return None

        if symbol.startswith("hk"):
            return {"market":"HK","code":symbol[2:],"name_en":f[0],"name_cn":f[1] if len(f)>1 else "",
                    "open":_f(f[2]),"prev_close":_f(f[3]),"price":_f(f[6]),
                    "high":_f(f[4]),"low":_f(f[5]),"change_pct":_f(f[8]),
                    "volume":_f(f[12]) if len(f)>12 else 0,"date":f[17] if len(f)>17 else ""}
        elif symbol.startswith("gb_"):
            return {"market":"US","code":symbol[3:],"name":f[0],"price":_f(f[1]),
                    "change_pct":_f(f[2]),"open":_f(f[5]),"high":_f(f[6]),
                    "low":_f(f[7]),"prev_close":_f(f[26]) if len(f)>26 else 0}
        else:
            return {"market":"A","code":symbol[2:],"name":f[0],"open":_f(f[1]),
                    "prev_close":_f(f[2]),"price":_f(f[3]),"high":_f(f[4]),
                    "low":_f(f[5]),"volume":_f(f[8]),"amount":_f(f[9]),
                    "date":f[30] if len(f)>30 else ""}
    except: return None


def _curl_commodity(symbol: str) -> dict | None:
    """Parse Sina quote for hf_ (commodity) tickers.
    Fields: [0]=price, [7]=prev_close, [12]=date, [13]=name
    """
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "8", f"https://hq.sinajs.cn/list={symbol}",
             "-H", "Referer: https://finance.sina.com.cn"],
            capture_output=True, timeout=10)
        raw = result.stdout.decode("gbk")
        if '="' not in raw: return None
        f = raw.split('="')[1].rstrip('";').split(",")
        if len(f) < 14: return None
        price = _f(f[0])
        prev = _f(f[7])
        return {
            "name": f[13],
            "price": price,
            "prev_close": prev,
            "change_pct": round((price - prev) / prev * 100, 2) if prev > 0 else 0,
            "date": f[12] if len(f) > 12 else "",
        }
    except: return None


def _curl_forex(symbol: str) -> dict | None:
    """Parse Sina quote for fx_ (forex) tickers.
    Fields: [1]=rate, [9]=name, [10]=change_pct
    """
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "8", f"https://hq.sinajs.cn/list={symbol}",
             "-H", "Referer: https://finance.sina.com.cn"],
            capture_output=True, timeout=10)
        raw = result.stdout.decode("gbk")
        if '="' not in raw: return None
        f = raw.split('="')[1].rstrip('";').split(",")
        if len(f) < 11: return None
        return {
            "name": f[9],
            "rate": _f(f[1]),
            "change_pct": _f(f[10]),
        }
    except: return None


def _f(val: str) -> float:
    try: return round(float(val), 3)
    except: return 0.0

def _detect_market(code: str) -> str:
    code = code.strip().upper().replace(".","").replace(" ","")
    if code.isdigit() and len(code) == 5: return f"hk{code}"
    if code.isdigit() and len(code) == 6:
        return f"sh{code}" if code.startswith(("60","68")) else f"sz{code}"
    return f"gb_{code.lower()}"


# ═══════════════════════════════════════════
#  MCP Tools
# ═══════════════════════════════════════════

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="stock_quote", description="获取股票实时行情。支持A股(6位代码)、港股(5位代码)、美股(字母代码)。自动识别市场。",
             inputSchema={"type":"object","properties":{"code":{"type":"string","description":"股票代码。A股:600519, 港股:00700, 美股:AAPL"}},"required":["code"]}),
        Tool(name="stock_search", description="按代码或名称搜索A股股票。返回最多10条匹配结果。",
             inputSchema={"type":"object","properties":{"query":{"type":"string","description":"搜索关键词（代码或名称）"}},"required":["query"]}),
        Tool(name="market_indices", description="获取主要市场指数：上证/深证/恒生/恒生科技/标普500/纳斯达克/道琼斯。",
             inputSchema={"type":"object","properties":{"market":{"type":"string","enum":["all","cn","hk","us"],"description":"市场: all(全部), cn(A股), hk(港股), us(美股)"}}}),
        Tool(name="stock_history", description="获取美股日K线历史数据（最近N天，最大200天）。",
             inputSchema={"type":"object","properties":{"code":{"type":"string","description":"美股ticker(小写)，如aapl"},"days":{"type":"integer","default":30}},"required":["code"]}),
        Tool(name="commodities", description="获取大宗商品实时价格：黄金(XAU)、白银(XAG)、WTI原油(CL)。",
             inputSchema={"type":"object","properties":{"item":{"type":"string","enum":["gold","silver","oil","all"],"description":"商品: gold(黄金), silver(白银), oil(WTI原油), all(全部)"}}}),
        Tool(name="forex", description="获取美元/人民币(USD/CNY)在岸汇率。",
             inputSchema={"type":"object","properties":{"pair":{"type":"string","enum":["usdcny"],"description":"货币对，目前仅支持 usdcny","default":"usdcny"}}}),
        Tool(name="macro_etfs", description="获取宏观情绪ETF：VIXY(恐慌指数)、UUP(美元)、TLT(长债)、SHY(短债)、GLD(黄金ETF)。",
             inputSchema={"type":"object","properties":{"etf":{"type":"string","enum":["all","vixy","uup","tlt","shy","gld"],"description":"ETF代码","default":"all"}}}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "stock_quote":
            c = arguments.get("code","").strip()
            if not c: return _text("请提供股票代码")
            d = _curl_sina(_detect_market(c))
            return _json(d or {"error": f"未找到 {c}"})

        elif name == "stock_search":
            q = arguments.get("query","").strip()
            if not q: return _text("请提供搜索词")
            conn = sqlite3.connect(str(DB_PATH))
            if q.isdigit():
                rows = conn.execute("SELECT code,name,industry_sw FROM stock_info WHERE code LIKE ? LIMIT 10", (f"%{q}%",)).fetchall()
            else:
                rows = conn.execute("SELECT code,name,industry_sw FROM stock_info WHERE name LIKE ? LIMIT 10", (f"%{q}%",)).fetchall()
            conn.close()
            return _json([{"code":r[0],"name":r[1],"industry":r[2] or ""} for r in rows])

        elif name == "market_indices":
            m = arguments.get("market","all")
            indices = {
                "cn": [("sh000001","上证指数"),("sz399001","深证成指"),("sz399006","创业板指")],
                "hk": [("hkHSI","恒生指数"),("hkHSTECH","恒生科技"),("hkHSCEI","国企指数")],
                "us": [("gb_inx","标普500"),("gb_ixic","纳斯达克"),("gb_dji","道琼斯")],
            }
            r = []
            for mk in (list(indices.keys()) if m=="all" else [m]):
                for sym, nm in indices.get(mk,[]):
                    d = _curl_sina(sym)
                    if d: d["index_name"] = nm; r.append(d)
                    time.sleep(0.1)
            return _json(r)

        elif name == "stock_history":
            code = arguments.get("code","").strip().lower()
            days = min(arguments.get("days",30), 200)
            try:
                url = f"https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getDailyK?symbol={code}&type=daily&length={days}"
                data = json.loads(subprocess.run(["curl","-s","--max-time","10",url,"-H","Referer: https://finance.sina.com.cn"], capture_output=True, timeout=12).stdout)
                if isinstance(data, list):
                    return _json([{"date":i.get("d",""),"open":float(i.get("o",0)),"high":float(i.get("h",0)),"low":float(i.get("l",0)),"close":float(i.get("c",0)),"volume":int(i.get("v",0))} for i in data[-days:]])
            except: pass
            return _text(f"无法获取 {code} 历史数据")

        elif name == "commodities":
            item = arguments.get("item","all")
            comms = {
                "gold":  ("hf_XAU", "黄金"),
                "silver":("hf_XAG", "白银"),
                "oil":   ("hf_CL",  "WTI原油"),
            }
            if item == "all":
                items = list(comms.values())
            else:
                items = [comms[item]] if item in comms else []
            r = []
            for sym, label in items:
                d = _curl_commodity(sym)
                if d: d["label"] = label; r.append(d)
                time.sleep(0.1)
            return _json(r)

        elif name == "forex":
            d = _curl_forex("fx_susdcny")
            return _json(d or {"error": "无法获取汇率"})

        elif name == "macro_etfs":
            etf = arguments.get("etf","all")
            etfs = {
                "vixy": ("gb_vixy", "恐慌指数ETF"),
                "uup":  ("gb_uup",  "美元指数ETF"),
                "tlt":  ("gb_tlt",  "20+年长债ETF"),
                "shy":  ("gb_shy",  "1-3年短债ETF"),
                "gld":  ("gb_gld",  "黄金ETF"),
            }
            if etf == "all":
                items = list(etfs.items())
            else:
                items = [(etf, etfs[etf])] if etf in etfs else []
            r = []
            for sym, label in items:
                d = _curl_sina(sym)
                if d: d["label"] = label; r.append(d)
                time.sleep(0.1)
            return _json(r)

        return _text(f"Unknown: {name}")
    except Exception as e:
        return _text(f"错误: {e}")


def _json(obj) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(obj, ensure_ascii=False, indent=2, default=str))]

def _text(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=msg)]


# ── Entry ──
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
