"""Minimal CLI for IndustryPulse."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from industrypulse.core import Settings
from industrypulse.core.console import configure_utf8

app = typer.Typer(add_completion=False, help="IndustryPulse 命令行工具")


def is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _stop_old_industrypulse_on_windows(port: int) -> bool:
    """若目标端口正被旧版 IndustryPulse 进程占用，则自动关闭并等待端口释放。"""
    if os.name != "nt":
        return False
    ps_script = (
        f"$conns = Get-NetTCPConnection -LocalPort {port} -State Listen "
        "-ErrorAction SilentlyContinue; "
        "foreach ($c in $conns) { "
        '  $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" '
        "-ErrorAction SilentlyContinue; "
        '  if ($p) { Write-Output "$($c.OwningProcess)|$($p.CommandLine)" } '
        "}"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    stopped_any = False
    self_pid = os.getpid()
    for line in proc.stdout.splitlines():
        if "|" not in line:
            continue
        pid_str, cmd = line.split("|", 1)
        try:
            pid = int(pid_str.strip())
        except ValueError:
            continue
        if pid == self_pid:
            continue
        if "industrypulse" in cmd.lower():
            typer.echo(
                f"[端口处理] 发现旧 IndustryPulse 实例 (PID {pid}) 占用端口 {port}，正在关闭..."
            )
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=5,
            )
            stopped_any = True
    if not stopped_any:
        return False
    for _ in range(15):
        if is_port_available("127.0.0.1", port):
            return True
        time.sleep(0.2)
    return is_port_available("127.0.0.1", port)


def resolve_serve_port(host: str, preferred_port: int) -> int:
    if is_port_available(host, preferred_port):
        return preferred_port
    if _stop_old_industrypulse_on_windows(preferred_port) and is_port_available(
        host, preferred_port
    ):
        typer.echo(f"[端口处理] 旧实例已关闭，继续使用端口 {preferred_port}")
        return preferred_port
    for candidate in range(preferred_port + 1, preferred_port + 21):
        if is_port_available(host, candidate):
            typer.echo(
                f"[端口处理] 端口 {preferred_port} 已被其他程序占用，自动切换至空闲端口 {candidate}"
            )
            return candidate
    raise typer.BadParameter(f"端口 {preferred_port}..{preferred_port + 20} 均不可用")


@app.callback()
def main() -> None:
    configure_utf8()


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="监听地址")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="监听端口")] = 8765,
    env_file: Annotated[Path | None, typer.Option(help="环境变量文件路径")] = None,
) -> None:
    """启动本地 FastAPI 工作台服务（自动处理旧实例端口占用）。"""
    actual_port = resolve_serve_port(host, port)
    typer.echo(f"访问地址: http://{host}:{actual_port}/")
    kwargs: dict[str, object] = {}
    if env_file and env_file.exists():
        kwargs["env_file"] = str(env_file)
    uvicorn.run("industrypulse.api.runtime:app", host=host, port=actual_port, **kwargs)  # type: ignore[arg-type]


@app.command("doctor")
def doctor() -> None:
    """输出当前运行环境与数据目录诊断信息。"""
    typer.echo(json.dumps(Settings.from_env().diagnostics(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
