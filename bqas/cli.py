"""BQAS CLI — 命令行入口

用法：
    bqas cache build            # 构建全市场财报缓存（首次运行，5-10分钟）
    bqas score 600519           # 单股评分（需先 build cache）
    bqas score 600519 --json    # JSON 输出
"""

import json
import sys
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="bqas")
def main():
    """BQAS — 巴菲特量化评估系统 MVP"""
    pass


@main.group()
def cache():
    """数据缓存管理"""
    pass


@cache.command("build")
@click.option("--years", default=5, help="拉取最近 N 年数据")
@click.option("--force", is_flag=True, help="强制重建缓存")
def cache_build(years: int, force: bool):
    """构建全市场财报缓存（首次运行需要 5-10 分钟）"""
    from .data.fetcher import build_full_cache
    build_full_cache(years=years, force=force)


@main.command()
@click.argument("code")
@click.option("--json", "json_output", is_flag=True, help="JSON 格式输出")
@click.option("--refresh", is_flag=True, help="强制刷新行情数据")
def score(code: str, json_output: bool, refresh: bool):
    """单股巴菲特量化评分"""
    from .engine.scorer import score_stock

    code = code.strip().zfill(6)

    with console.status(f"[bold yellow]正在分析 {code}...[/bold yellow]"):
        result = score_stock(code, force_refresh=refresh)

    if json_output:
        simplified = {
            "code": result["code"],
            "name": result["name"],
            "industry": result.get("industry_group", ""),
            "passed_blacklist": result["passed_blacklist"],
            "total": result["total"],
            "rating": result["rating"],
            "rating_label": result["rating_label"],
            "rating_advice": result["rating_advice"],
        }
        if not result["passed_blacklist"]:
            simplified["blacklist_reason"] = result.get("blacklist_reason", "")
        else:
            simplified["scores"] = result["scores"]["weighted"]
        console.print(json.dumps(simplified, ensure_ascii=False, indent=2))
        return

    name = result["name"]
    industry = result.get("industry_group", "未知")

    title = Text(f"{name} ({code})", style="bold cyan")
    console.print(Panel(title, subtitle=f"行业: {industry}"))

    if not result["passed_blacklist"]:
        console.print(f"[red]⛔ 一票否决: {result.get('blacklist_reason', '')}[/red]")
        return

    rating_color = "green" if result["total"] >= 75 else "yellow" if result["total"] >= 65 else "red"
    console.print(Panel(
        f"[bold {rating_color}]{result['rating']}  {result['total']:.1f} 分 — {result['rating_label']}[/bold {rating_color}]\n"
        f"[dim]{result['rating_advice']}[/dim]"
    ))

    table = Table(title="维度得分明细", show_header=True, header_style="bold")
    table.add_column("维度", style="cyan")
    table.add_column("得分", justify="right")
    table.add_column("满分", justify="right")

    w = result["scores"]["weighted"]
    max_scores = {"quality": 35, "value": 30, "health": 20, "gov": 15}
    table.add_row("① 企业质量", f"{w['quality']:.1f}", str(max_scores["quality"]))
    table.add_row("② 估值水平", f"{w['value']:.1f}", str(max_scores["value"]))
    table.add_row("③ 财务健康", f"{w['health']:.1f}", str(max_scores["health"]))
    table.add_row("④ 治理",     f"{w['gov']:.1f}", str(max_scores["gov"]))
    table.add_row("[bold]总分[/bold]", f"[bold]{w['total']:.1f}[/bold]", "100")

    console.print(table)


if __name__ == "__main__":
    main()
