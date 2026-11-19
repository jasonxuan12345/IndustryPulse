"""Minimal CLI for IndustryPulse."""

from __future__ import annotations

import json

import typer
import uvicorn

from industrypulse.core import Settings
from industrypulse.core.console import configure_utf8

app = typer.Typer(add_completion=False, help="IndustryPulse 命令行工具")


@app.callback()
def main() -> None:
    configure_utf8()


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="监听地址"),
    port: int = typer.Option(8765, help="监听端口"),
) -> None:
    """启动本地 FastAPI 工作台服务。"""
    uvicorn.run("industrypulse.api.runtime:app", host=host, port=port)


@app.command("doctor")
def doctor() -> None:
    """输出当前运行环境与数据目录诊断信息。"""
    typer.echo(json.dumps(Settings.from_env().diagnostics(), ensure_ascii=False, indent=2))
