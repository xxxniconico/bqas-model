#!/usr/bin/env python3
"""BQAS — Streamlit Cloud 看板 (原生版)"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import pandas as pd

from bqas.data.fetcher import fetch_financials
from bqas.engine.scorer import score_stock

st.set_page_config(page_title="BQAS 巴菲特量化", page_icon="📊", layout="wide")
st.title("📊 BQAS 巴菲特量化评分系统")

# ── Sidebar ──
with st.sidebar:
    st.header("🔍 股票评分")
    code = st.text_input("输入股票代码", placeholder="600519", max_chars=6)
    if st.button("评分", type="primary") and code:
        with st.spinner(f"正在分析 {code}..."):
            try:
                result = score_stock(code)
                st.session_state["score_result"] = result
                st.session_state["score_code"] = code
            except Exception as e:
                st.error(f"评分失败: {e}")

    st.divider()
    st.caption("BQAS V2.3 — 纯年报多因子模型")
    st.caption("[GitHub](https://github.com/xxxniconico/bqas-model)")

# ── Main ──
tab1, tab2, tab3 = st.tabs(["📈 个股评分", "🏆 Top 30", "📋 行业概览"])

# Tab 1: Score
with tab1:
    if "score_result" in st.session_state:
        r = st.session_state["score_result"]
        code = st.session_state["score_code"]
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("总评分", f"{r.get('total_score', 0):.0f}", help="满分100")
        with col2:
            st.metric("质量", f"{r.get('quality_score', 0):.0f}")
        with col3:
            st.metric("价值", f"{r.get('value_score', 0):.0f}")
        with col4:
            st.metric("健康度", f"{r.get('health_score', 0):.0f}")
        
        if "factors" in r:
            st.subheader("因子明细")
            factors = r["factors"]
            df = pd.DataFrame([
                {"因子": k, "数值": f"{v:.2f}" if isinstance(v, float) else str(v)}
                for k, v in factors.items()
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)
        
        if "annual_data" in r:
            st.subheader("年度数据")
            st.dataframe(pd.DataFrame(r["annual_data"]), use_container_width=True)
    else:
        st.info("👈 在左侧输入股票代码，点击「评分」开始")

# Tab 2: Top 30
with tab2:
    st.info("🏗️ Top 30 排行需要本地跑 batch_rank3.py 生成数据。Streamlit Cloud 暂不支持实时全市场扫描（需 2.5 分钟 + 数据库）。")
    st.markdown("本地使用: `python batch_rank3.py`")

# Tab 3: Industry
with tab3:
    st.info("🏗️ 行业概览同上，需本地运行 `batch_rank3.py --industry`")
