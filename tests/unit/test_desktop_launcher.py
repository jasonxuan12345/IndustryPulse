from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows desktop lifecycle")


@pytest.mark.parametrize("abrupt", [False, True], ids=["normal-close", "launcher-killed"])
def test_launcher_closes_entire_process_tree(tmp_path: Path, abrupt: bool) -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    marker = tmp_path / "children.json"
    child_code = (
        "import json, os, subprocess, sys, time; "
        "from pathlib import Path; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(), child.pid])); "
        "time.sleep(120)"
    )
    parent_code = (
        "import sys; "
        f"sys.path.insert(0, {str(scripts)!r}); "
        "from launch_dashboard import ProcessJob; "
        "job = ProcessJob(); "
        f"job.start([sys.executable, '-c', {child_code!r}], hidden=True); "
        "input(); job.close()"
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles = []
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdin=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        process_ids = None
        while time.monotonic() < deadline:
            assert parent.poll() is None, "Launcher exited before creating children"
            try:
                process_ids = json.loads(marker.read_text())
                break
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.05)
        assert process_ids is not None, "Child processes did not start"
        for process_id in process_ids:
            handle = kernel.OpenProcess(0x00100000, False, process_id)
            assert handle, f"Child process {process_id} is not running"
            handles.append(handle)
        if abrupt:
            parent.terminate()
            parent.wait(timeout=10)
        else:
            parent.communicate("\n", timeout=10)
            assert parent.returncode == 0
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 5000) == 0, "Child process survived launcher"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        if parent.stdin and not parent.stdin.closed:
            parent.stdin.close()
        for handle in handles:
            kernel.CloseHandle(handle)
