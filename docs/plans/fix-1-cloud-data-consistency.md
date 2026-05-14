# BQAS Streamlit Cloud 数据一致性修复计划

**日期**: 2026-05-14
**规划者**: Hermes（探索者/计划者）
**执行者**: OpenCode（工作者/校验者）

---

## 问题

线上 Streamlit Cloud 看板数据与本地 `localhost:8503` 不一致：
1. 排行榜分数不同（batch_rank3 vs score_stock）
2. 部分股票缺失（enrich 失败跳过）
3. 个股详情结构不完整（缺 DuPont/Beneish 等嵌套数据）

## 根因

Cloud 版用 `score_stock()` 逐个算分，而本地用 `factor_cache.json`（batch_rank3 生成）。
`score_stock()` 是单年度模型，`batch_rank3` 是多年度平滑，口径不同。

用户明确要求：**排行榜与个股评分数据必须一致**。

## 方案

### 核心思路

让 Cloud 版的 `streamlit_app.py` **完全复制**本地 `api_server.py` 的数据逻辑：

1. **排行榜 /api/screener** → 用 `factor_cache.json` + V维度实时计算（与本地完全一致）
2. **个股评分 /api/score** → 用 `score_stock()` + Beneish（与本地完全一致）
3. **数据文件** → 本地生成 `factor_cache.json` 提交到仓库，Cloud 直接读取

### 需要执行的任务

#### 任务 1：本地生成 factor_cache.json 并提交

```bash
cd ~/bqas-model
python -c "
from bqas.engine.cache_layer import build_factor_cache
build_factor_cache(force=True)
"
# 确认 bqas/data/cache/factor_cache.json 已生成
# git add + commit + push
```

#### 任务 2：重写 streamlit_app.py

新版 streamlit_app.py 应该：

1. **不再预计算数据**（删除 dashboard_data.json 生成逻辑）
2. **直接读取 factor_cache.json**，复制 api_server.py 的 `/api/screener` 逻辑
3. **对 30 只预选股票**，调用 `score_stock()` + Beneish 生成 `score_data`
4. **嵌入 dashboard.html**，fetch 拦截器从内存数据返回

**关键约束：**
- akshare 不可用，所有数据必须来自本地 DB 或预计算文件
- factor_cache.json 中已有 Q/H/G 维度分数
- V 维度需要从 DB 实时算（同 api_server.py lines 96-199，约 100 行代码）
- 仅处理 A 股（cn market）

**数据结构要求：**

```python
# screener 返回格式（匹配 dashboard.html 表格渲染）:
[
  {
    "code": "600519", "name": "贵州茅台",
    "total": 82.6,     # 总分
    "q_score": 28.9,   # 质量分 (满分35)
    "v_score": 24.8,   # 估值分 (满分30)
    "h_score": 16.5,   # 健康分 (满分20)
    "g_score": 12.4,   # 治理分 (满分15)
    "roe_pct": 33.2,   # ROE百分比
    "industry_sw": "白酒"
  }, ...
]

# score_data 格式（匹配 /api/score/ 返回）:
{
  "600519": {
    "code": "600519", "name": "贵州茅台",
    "total": 82.6,
    "rating": "⭐⭐⭐", "rating_label": "优秀", "rating_advice": "...",
    "industry_sw": "白酒", "industry_group": "消费",
    "passed_blacklist": true, "blacklist_reason": "",
    "blacklist_checks": {"audit_opinion": "标准无保留", ...},
    "scores": {
      "weighted": {"quality": 28.9, "value": 24.8, "health": 16.5, "gov": 12.4, "total": 82.6},
      "quality": {"roe": {"score": 10, "roe_5y": 0.33, "dupont": {...}}, ...},
      ...
    },
    "beneish": {"m_score": -2.9, "likely_manipulator": false, ...},
    "factors": {"roe_3y": 0.332, "revenue_cagr_3y": 0.168, ...}
  }, ...
}
```

#### 任务 3：验证

```bash
# 本地验证 streamlit_app.py
cd ~/bqas-model
streamlit run streamlit_app.py --server.port 8505
# 打开 http://localhost:8505 对比 http://localhost:8503
# 检查：
#  - 排行榜 Top 30 分数是否一致
#  - 点击个股评分详情是否一致
#  - 行业概览是否一致
```

### 文件变更清单

| 文件 | 操作 |
|------|------|
| `bqas/data/cache/factor_cache.json` | 新增到 git |
| `streamlit_app.py` | 重写 |
| `dashboard_data.json` | 废弃，删除 |
| `requirements.txt` | 可能需要加 numpy/pandas |

### 预计工作量

- 任务 1：1 分钟（跑命令 + push）
- 任务 2：主体工作，约 100 行 Python，复制 api_server.py 逻辑
- 任务 3：5 分钟对比验证
