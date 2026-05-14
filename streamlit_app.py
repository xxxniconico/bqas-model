#!/usr/bin/env python3
"""BQAS — Streamlit Cloud 看板 (静态预计算版)
akshare 在 Cloud 上不可用，使用本地预计算的 JSON 数据。
"""
import json
from pathlib import Path

import streamlit as st
import pandas as pd

st.set_page_config(page_title="BQAS 巴菲特量化", page_icon="📊", layout="wide")
st.title("📊 BQAS 巴菲特量化评分系统")

# ── Load pre-computed data ──
DATA_FILE = Path(__file__).parent / "dashboard_data.json"

@st.cache_data
def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return None

data = load_data()

# ── Sidebar ──
with st.sidebar:
    st.header("📊 关于 BQAS")
    st.markdown("""
    **V2.3 纯年报多因子模型**
    
    - 质量 / 价值 / 健康度 / 治理 四维评分
    - Spearman 稳定性 0.81
    - 全 A 股 5500+ 标的覆盖
    
    [📖 GitHub](https://github.com/xxxniconico/bqas-model)
    """)
    if data:
        st.metric("覆盖股票", f"{data.get('total_stocks', 0):,}")
        st.metric("数据日期", data.get("date", "N/A"))
    else:
        st.warning("暂无预计算数据")

# ── Main Tabs ──
tab1, tab2, tab3 = st.tabs(["🏆 Top 30", "📋 行业概览", "📈 模型说明"])

with tab1:
    if data and "top30" in data:
        top = pd.DataFrame(data["top30"])
        cols = ["rank", "code", "name", "industry", "total_score", "quality", "value", "health", "governance"]
        display_cols = ["排名", "代码", "名称", "行业", "总分", "质量", "价值", "健康度", "治理"]
        available = [c for c in cols if c in top.columns]
        
        df = top[available].copy()
        df.columns = [display_cols[cols.index(c)] for c in available]
        
        st.dataframe(df, use_container_width=True, hide_index=True, height=900)
        
        # Score distribution
        if "total_score" in top.columns:
            st.subheader("评分分布")
            st.bar_chart(top.set_index("name")["total_score"].head(20))
    else:
        st.info("""
        🏗️ **数据未生成** — 需要在本地运行预计算脚本：
        
        ```bash
        cd ~/bqas-model
        python batch_rank3.py --top 30 --output dashboard_data.json
        ```
        
        然后将 `dashboard_data.json` 提交到仓库即可。
        """)

with tab2:
    if data and "industries" in data:
        ind = pd.DataFrame(data["industries"])
        st.dataframe(ind, use_container_width=True, hide_index=True)
    else:
        st.info("行业数据同上，需本地生成。")

with tab3:
    st.markdown("""
    ### 评分体系
    
    | 维度 | 权重 | 核心指标 |
    |------|------|----------|
    | 质量 | 35% | ROE, ROIC, 毛利率, 营收增速 |
    | 价值 | 30% | FCF收益率, EV/EBIT, P/B |
    | 健康度 | 20% | 杠杆率, 利息覆盖, 流动比率 |
    | 治理 | 15% | 审计意见, 质押率, 分红, 处罚 |
    
    ### 回测表现
    
    | 指标 | 结果 |
    |------|------|
    | 单年 Spearman | 0.59–0.70 |
    | 多年稳定性 | 0.81 |
    | 2024→2025 超额收益 | +2.6% |
    
    ### 黑名单过滤
    
    11 项检查：审计意见、ST、处罚、质押率>50%、分红异常、商誉/营收>30% 等
    """)
