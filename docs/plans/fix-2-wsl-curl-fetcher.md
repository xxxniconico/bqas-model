# BQAS fetcher.py WSL 兼容性修复计划

**日期**: 2026-05-15
**规划者**: Hermes
**执行者**: Cursor

---

## 问题

`fetcher.py` 的 `build_full_cache()` 依赖 akshare：
- `ak.stock_lrb_em()` → **超时**（WSL 下 curl 同 API 正常）
- `ak.stock_zcfz_em()` → ✅ 正常
- `ak.stock_xjll_em()` → ✅ 正常
- `ak.stock_info_a_code_name()` → ✅ 正常

根因：WSL 下 akshare 的 HTTP stack 间歇性超时，但 curl 直连东方财富 API 稳如老狗。

## 方案

在 `fetcher.py` 加一个 `_fetch_eastmoney(table, report_date)` 函数，用 `subprocess.run(["curl", ...])` 替代 akshare。

只替换 `stock_lrb_em`（利润表），其他保持 akshare。

## 具体修改

### 1. 新增函数 `_fetch_eastmoney_income(report_date: str) -> pd.DataFrame`

```python
def _fetch_eastmoney_income(report_date: str) -> Optional[pd.DataFrame]:
    """用 curl 直连东方财富 Data Center 拉利润表。
    
    返回 DataFrame，列名与 akshare 保持一致：
    股票代码, 股票简称, 营业总收入, 营业利润, 净利润, 营业总支出-财务费用, 营业总支出-营业支出
    """
    import subprocess, json
    
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        "?reportName=RPT_DMSK_FN_INCOME"
        "&columns=SECURITY_CODE,SECURITY_NAME_ABBR,TOTAL_OPERATE_INCOME,"
        "OPERATE_PROFIT,PARENT_NETPROFIT,FINANCIAL_EXPENSE,OPERATE_COST"
        f"&pageSize=6000&sortColumns=NOTICE_DATE&sortTypes=-1"
        f"&filter=(REPORT_DATE='{report_date}')"
    )
    
    result = subprocess.run(
        ["curl", "-s", "--max-time", "30", url],
        capture_output=True, timeout=35
    )
    data = json.loads(result.stdout)
    if not data.get("success") or not data.get("result", {}).get("data"):
        return None
    
    rows = []
    for item in data["result"]["data"]:
        rows.append({
            "股票代码": str(item.get("SECURITY_CODE", "")).zfill(6),
            "股票简称": item.get("SECURITY_NAME_ABBR", ""),
            "营业总收入": float(item.get("TOTAL_OPERATE_INCOME", 0) or 0),
            "营业利润": float(item.get("OPERATE_PROFIT", 0) or 0),
            "净利润": float(item.get("PARENT_NETPROFIT", 0) or 0),
            "营业总支出-财务费用": float(item.get("FINANCIAL_EXPENSE", 0) or 0),
            "营业总支出-营业支出": float(item.get("OPERATE_COST", 0) or 0),
        })
    
    return pd.DataFrame(rows)
```

### 2. 修改 `build_full_cache()` 第 280 行

```python
# 原代码（第280行）:
df_income = ak.stock_lrb_em(date=period)

# 改为:
try:
    time.sleep(RATE_LIMIT_SLEEP)
    df_income = ak.stock_lrb_em(date=period)
except Exception:
    # akshare fallback: curl 直连东方财富
    df_income = _fetch_eastmoney_income(period)
```

### 3. 验证

```bash
cd ~/bqas-model
python -c "
from bqas.data.fetcher import _fetch_eastmoney_income
df = _fetch_eastmoney_income('20241231')
assert df is not None, 'fetch failed'
assert len(df) > 5000, f'only {len(df)} stocks'
assert '营业总收入' in df.columns
print(f'OK: {len(df)} stocks, columns={df.columns.tolist()}')
"
```

## 文件变更

| 文件 | 操作 |
|------|------|
| `bqas/data/fetcher.py` | 新增 `_fetch_eastmoney_income()` + 修改 line 280 |
| `.gitignore` | 确保 `bqas/data/cache/bqas.db` 已忽略 |

## 约束

- ⚠️ 只改 profit/income 拉取，不动 balance/cashflow（它们工作正常）
- ⚠️ 不删 `_clear_cache()` 调用（即使 DB 空也是用户手动触发）
- ⚠️ 不修改任何 `_bulk_insert_*` 函数
- ⚠️ 完成后运行验证命令确认
