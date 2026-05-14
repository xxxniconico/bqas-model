#!/usr/bin/env python3
"""BQAS — Streamlit Cloud: 嵌入真实 dashboard.html + 预计算数据注入"""
import json
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="BQAS 巴菲特量化", page_icon="📊", layout="wide")

# Load dashboard and data
html_path = Path(__file__).parent / "dashboard.html"
data_path = Path(__file__).parent / "dashboard_data.json"

with open(html_path, encoding="utf-8") as f:
    html = f.read()

with open(data_path) as f:
    data = json.load(f)

# Inject data + fetch interceptor
injection = f"""<script>
window._BQAS_DATA = {json.dumps(data, ensure_ascii=False)};
(function(){{
    const _fetch = window.fetch;
    const D = window._BQAS_DATA;
    window.fetch = function(url, opts) {{
        const u = String(url);
        if (u.includes('/api/health'))
            return Promise.resolve({{json:()=>Promise.resolve(D.health||{{status:'ok'}})}});
        if (u.includes('/api/screener') || u.includes('/api/screener-global'))
            return Promise.resolve({{json:()=>Promise.resolve(D.screener||[])}});
        if (u.includes('/api/industry-summary') || u.includes('/api/industry-summary-global'))
            return Promise.resolve({{json:()=>Promise.resolve(D.industry_summary||[])}});
        if (u.includes('/api/search')) {{
            const q = new URL(u,'http://x').searchParams.get('q')||'';
            const results = (D.search_index||[]).filter(s=>
                s.code.includes(q) || s.name.includes(q) || s.industry.includes(q)
            );
            return Promise.resolve({{json:()=>Promise.resolve(results)}});
        }}
        if (u.includes('/api/compare')) {{
            const codesStr = u.split('codes=')[1]?.split('&')[0]||'';
            const codes = codesStr.split(',');
            const results = (D.compare||[]).filter(s=>codes.includes(s.code));
            return Promise.resolve({{json:()=>Promise.resolve(results)}});
        }}
        if (u.includes('/api/score/') || u.includes('/api/score-global/')) {{
            const code = u.split('/').pop().split('?')[0];
            const stock = (D.screener||[]).find(s=>s.code===code);
            return Promise.resolve({{json:()=>Promise.resolve(stock||{{error:'not found'}})}});
        }}
        if (u.includes('/api/stock-profile/')) {{
            const code = u.split('/').pop();
            const stock = (D.screener||[]).find(s=>s.code===code);
            return Promise.resolve({{json:()=>Promise.resolve(stock||{{}})}});
        }}
        return _fetch.call(window, url, opts);
    }};
}})();
</script>
"""

html = html.replace('</head>', injection + '\n</head>')

st.components.v1.html(html, height=2400, scrolling=True)
