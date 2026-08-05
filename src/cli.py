"""E-Commerce Harness — Typer CLI"""

import asyncio
import base64
import os
import sys
from pathlib import Path
from typing import Optional

import typer

# Windows: 强制 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(name="ecommerce-harness", help="群聊式多智能体电商商品图生成系统")


@app.command()
def run(
    image: str = typer.Argument(..., help="商品图片路径"),
    platform: str = typer.Option("taobao", help="目标电商平台"),
    product_info: str = typer.Option("", help="商品补充信息"),
    category: str = typer.Option("", help="品类提示（保健品/化妆品/3C等）"),
    mode: str = typer.Option("serial", help="协作模式: serial / ab_generate / debate / vote"),
    max_turns: int = typer.Option(15, help="最大群聊轮次"),
):
    """上传单张商品图片，运行群聊生图流程"""
    _sync_run(image_path=image, platform=platform, product_info=product_info,
              category=category, mode=mode, max_turns=max_turns)


@app.command()
def batch(
    directory: str = typer.Argument(..., help="图片目录路径"),
    platform: str = typer.Option("taobao", help="目标电商平台"),
    max_turns: int = typer.Option(15, help="最大群聊轮次"),
):
    """批量处理目录下的所有商品图片"""
    dir_path = Path(directory)
    if not dir_path.exists():
        typer.echo(f"[ERROR] 目录不存在: {directory}")
        raise typer.Exit(1)

    images = sorted([
        f for f in dir_path.glob("*")
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    ])
    typer.echo(f"找到 {len(images)} 张图片")

    for img in images:
        typer.echo(f"\n{'='*50}")
        typer.echo(f"处理: {img.name}")
        typer.echo(f"{'='*50}")
        _sync_run(image_path=str(img), platform=platform, max_turns=max_turns)


@app.command()
def agents_list():
    """列出所有已注册的 Agent"""
    from src.agents.registry import get_agent_registry
    from src.providers import get_provider_registry

    async def _list():
        registry = get_agent_registry()
        provider_registry = get_provider_registry()
        await registry.load_from_config(provider_registry)

        for meta in registry.list_all():
            typer.echo(f"  [Agent] {meta.name} v{meta.version}")
            typer.echo(f"     {meta.description}")
            typer.echo(f"     requires: {', '.join(meta.requires)}")
            if meta.params:
                for p in meta.params:
                    opts = f" [{', '.join(p.get('options', []))}]" if p.get('options') else ""
                    typer.echo(f"     param: {p['label']} ({p['type']}){opts} -> default: {p.get('default', '')}")

    asyncio.run(_list())


@app.command()
def config_validate():
    """验证所有配置文件"""
    from src.core.config import load_default_config, load_models_config, list_agent_configs, load_agent_config

    errors = []
    typer.echo("[验证] 检查配置文件...")

    # default.yaml
    try:
        cfg = load_default_config()
        typer.echo(f"  [OK] config/default.yaml -- {len(cfg)} top-level keys")
    except Exception as e:
        errors.append(f"config/default.yaml: {e}")

    # models.yaml
    try:
        cfg = load_models_config()
        caps = cfg.get("capabilities", {})
        typer.echo(f"  [OK] config/models.yaml -- {len(caps)} capabilities")
    except Exception as e:
        errors.append(f"config/models.yaml: {e}")

    # agent configs
    agent_names = list_agent_configs()
    for name in agent_names:
        try:
            cfg = load_agent_config(name)
            typer.echo(f"  [OK] config/agents/{name}.yaml -- {cfg.get('name', '?')} (requires: {cfg.get('requires', [])})")
        except Exception as e:
            errors.append(f"config/agents/{name}.yaml: {e}")

    if errors:
        typer.echo(f"\n[ERROR] {len(errors)} errors:")
        for e in errors:
            typer.echo(f"  - {e}")
    else:
        typer.echo(f"\n[OK] All {len(agent_names) + 2} config files validated")


def _sync_run(image_path: str, platform: str = "taobao", product_info: str = "", category: str = "", mode: str = "serial", max_turns: int = 15):
    """同步执行群聊流程（CLI 入口）"""
    from src.providers import get_provider_registry
    from src.agents.registry import get_agent_registry
    from src.chat.session import SessionManager
    from src.chat.engine import ChatEngine

    async def _go():
        # 读取图片
        path = Path(image_path)
        if not path.exists():
            typer.echo(f"[ERROR] 图片不存在: {image_path}")
            raise typer.Exit(1)

        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        # 初始化
        provider_registry = get_provider_registry()
        agent_registry = get_agent_registry()
        await agent_registry.load_from_config(provider_registry)

        session_mgr = SessionManager()
        session = session_mgr.create(
            product_images=[b64],
            product_info=product_info,
            platform=platform,
            category_hint=category,
            max_turns=max_turns,
            collaboration_mode=mode,
        )

        engine = ChatEngine(registry=agent_registry, session_manager=session_mgr)
        result = await engine.run(session)

        # 输出结果
        typer.echo(f"\n{'='*50}")
        typer.echo(f"[Done] session_id={result['session_id']}")
        typer.echo(f"   status: {result['status']}")
        typer.echo(f"   turns: {result['turn_count']}")
        typer.echo(f"{'='*50}")

        artifacts = result.get("artifacts", {})
        if artifacts.get("analysis"):
            analysis = artifacts["analysis"]
            typer.echo(f"\n[Analysis] category: {analysis.get('category', 'unknown')}")
            typer.echo(f"   confidence: {analysis.get('confidence_score', 0)}%")
        if artifacts.get("review"):
            review = artifacts["review"]
            typer.echo(f"\n[Review] score: {review.get('overall_score', 0)}/100")
            typer.echo(f"   verdict: {review.get('verdict', 'unknown')}")
        if artifacts.get("compliance"):
            comp = artifacts["compliance"]
            passed = "PASS" if comp.get('passed') else "FAIL"
            typer.echo(f"\n[Compliance] {passed}")
            typer.echo(f"   risk: {comp.get('risk_level', 'unknown')}")

        # 输出群聊摘要
        messages = result.get("messages", [])
        typer.echo(f"\n--- Messages ({len(messages)}) ---")
        for m in messages:
            sender = m.get("sender", "?")
            action = m.get("action", "?")
            content_preview = str(m.get("content", ""))[:80]
            typer.echo(f"  [{sender}] {action}: {content_preview}")

    asyncio.run(_go())


if __name__ == "__main__":
    app()
