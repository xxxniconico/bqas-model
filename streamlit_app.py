#!/usr/bin/env python3
"""BQAS — Streamlit Cloud: 嵌入真实 dashboard.html + 预计算数据注入"""
import json
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="BQAS 巴菲特量化", page_icon="📊", layout="wide")

html_path = Path(__file__).parent / "dashboard.html"
data_path = Path(__file__).parent / "dashboard_data.json"

with open(html_path, encoding="utf-8") as f:
    html = f.read()

with open(data_path) as f:
    data = json.load(f)

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

html = html.replace('</head>', injection + '\n</head>')

st.components.v1.html(html, height=2400, scrolling=True)
