"""Windows desktop lifecycle: closing the launcher kills its entire process job."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


class BasicLimit(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "read_operations",
            "write_operations",
            "other_operations",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimit),
        ("io", IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("x_size", wintypes.DWORD),
        ("y_size", wintypes.DWORD),
        ("x_count", wintypes.DWORD),
        ("y_count", wintypes.DWORD),
        ("fill", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("show", wintypes.WORD),
        ("reserved_size", wintypes.WORD),
        ("reserved_pointer", ctypes.POINTER(wintypes.BYTE)),
        ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE),
        ("stderr", wintypes.HANDLE),
    ]


class ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("process_id", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
    ]


class ProcessJob:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("This desktop launcher requires Windows.")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel.ResumeThread.restype = wintypes.DWORD
        self.kernel.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(StartupInfo),
            ctypes.POINTER(ProcessInfo),
        ]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.processes: list[ProcessInfo] = []
        limit = ExtendedLimit()
        limit.basic.flags = 0x2000
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)
        ):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self, command: list[str], *, hidden: bool = False) -> ProcessInfo:
        startup = StartupInfo()
        startup.size = ctypes.sizeof(startup)
        process = ProcessInfo()
        flags = 0x00000004 | (0x08000000 if hidden else 0)
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        if not self.kernel.CreateProcessW(
            None,
            command_line,
            None,
            None,
            False,
            flags,
            None,
            str(ROOT),
            ctypes.byref(startup),
            ctypes.byref(process),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process.process):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.kernel.ResumeThread(process.thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.kernel.TerminateProcess(process.process, 1)
            self.kernel.CloseHandle(process.process)
            raise
        finally:
            self.kernel.CloseHandle(process.thread)
        self.processes.append(process)
        return process

    def running(self, process: ProcessInfo) -> bool:
        code = wintypes.DWORD()
        return (
            bool(self.kernel.GetExitCodeProcess(process.process, ctypes.byref(code)))
            and code.value == 259
        )

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        for process in self.processes:
            self.kernel.WaitForSingleObject(process.process, 5000)
            self.kernel.CloseHandle(process.process)
        self.processes.clear()


def browser_path() -> str:
    candidates = []
    for folder in (
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if folder:
            candidates.extend(
                [
                    Path(folder) / "Microsoft/Edge/Application/msedge.exe",
                    Path(folder) / "Google/Chrome/Application/chrome.exe",
                ]
            )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Microsoft Edge or Google Chrome is required for the application window.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    options = parser.parse_args()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", options.port)) == 0:
            print(f"Port {options.port} is already in use. Close the existing launcher first.")
            return 1
    browser = browser_path() if not options.no_browser else None
    directory = ROOT / "artifacts/dashboard"
    directory.mkdir(parents=True, exist_ok=True)
    session_path = directory / "session.json"
    job = ProcessJob()
    try:
        server = job.start(
            [
                sys.executable,
                str(ROOT / "scripts/dashboard_server.py"),
                "--port",
                str(options.port),
            ],
            hidden=True,
        )
        address = f"http://127.0.0.1:{options.port}"
        print(f"Starting IndustryPulse: {address}", flush=True)
        print(
            "Keep this window open. Close it or press Ctrl+C "
            "to stop the app and all background tasks.",
            flush=True,
        )
        ready = False
        for _ in range(90):
            if not job.running(server):
                raise RuntimeError("Backend failed to start. See artifacts/dashboard/server.log.")
            try:
                with urlopen(address + "/healthz", timeout=1) as response:
                    if response.status == 200:
                        ready = True
                        break
            except (URLError, TimeoutError, OSError):
                time.sleep(0.5)
        if not ready:
            raise RuntimeError("Backend startup timed out. See artifacts/dashboard/server.log.")
        frontend = (
            job.start(
                [
                    browser,
                    f"--app={address}",
                    f"--user-data-dir={directory / 'browser-profile'}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-mode",
                ]
            )
            if browser
            else None
        )
        session_path.write_text(
            json.dumps(
                {
                    "launcher_pid": os.getpid(),
                    "server_pid": server.process_id,
                    "browser_pid": frontend.process_id if frontend else None,
                    "port": options.port,
                }
            ),
            encoding="utf-8",
        )
        print(
            "IndustryPulse is running. Closing the application window also stops the backend.",
            flush=True,
        )
        while job.running(server) and (frontend is None or job.running(frontend)):
            time.sleep(0.4)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        job.close()
        session_path.unlink(missing_ok=True)
        print("IndustryPulse stopped. Frontend and background processes are closed.", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Launch failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
