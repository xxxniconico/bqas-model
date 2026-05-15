# BQAS fetcher.py 错误处理加固

**日期**: 2026-05-15
**规划者**: Hermes
**执行者**: Cursor

---

## 问题

`fetcher.py` 中多处 `except: continue/pass` 静默吞掉错误，导致 bug 排查困难。

## 任务

### 1. 审计所有 except 块

搜索 `fetcher.py` 中所有 `except`，找出静默吞错的：

```bash
grep -n "except" bqas/data/fetcher.py
```

### 2. 修复规则

| 当前写法 | 改为 |
|----------|------|
| `except Exception: continue` | `except Exception as e: print(f"  ⚠ row insert: {e}", file=sys.stderr); continue` |
| `except Exception: pass` | 同上 |
| `except: pass` | 同上，但先改成 `except Exception` |
| `except SomeError: return None` | 保持不变（有意返回 None） |

### 3. 不改的

- `build_full_cache()` 中已有的 `except ... as e: print(...)` — 已经够好了
- `_fetch_eastmoney_income()` 刚修过 — 不动
- 函数级别的 `except` 返回 None — 这是有意的 fallback 行为

### 4. 自我检查

改完后跑一轮 `build_full_cache(force=False)` 或至少跑 `score_stock("600519")`，确认：
- 没有新报错
- 旧报错能被看到
- 数据能正常写入

### 5. 验证命令

```bash
cd ~/bqas-model
python -c "
from bqas.engine.scorer import score_stock
r = score_stock('600519')
print(f'Score: {r.get(\"total\",0)}')
"
```

期望看到贵州茅台有分数（不需要完整，只要有就行）。

## 约束

- ⚠️ 只改 `except` 块的报错输出，不改逻辑
- ⚠️ 不改 `bqas.db`
- ⚠️ 改完跑验证
