from __future__ import annotations

import ctypes
import os
import platform
from typing import Any, Dict


def probe_hw_profile() -> Dict[str, Any]:
    cpu_cores = _int_env("BABYAI_CPU_CORES", default=(os.cpu_count() or 1))
    ram_gb = _int_env("BABYAI_RAM_GB", default=_detect_ram_gb())
    gpu_count = _int_env("BABYAI_GPU_COUNT", default=0)
    gpu_model = str(os.getenv("BABYAI_GPU_MODEL", "")).strip()
    gpu_vram_gb = _int_env("BABYAI_GPU_VRAM_GB", default=0)

    profile_name = "cpu_only"
    if gpu_count > 0:
        if "m6000" in gpu_model.lower() and ram_gb >= 512:
            profile_name = "m6000_512gb"
        else:
            profile_name = "gpu_general"

    return {
        "profile_name": profile_name,
        "hostname": platform.node(),
        "os": platform.system().lower(),
        "cpu_cores": max(1, int(cpu_cores)),
        "ram_gb": max(1, int(ram_gb)),
        "gpu_count": max(0, int(gpu_count)),
        "gpu_model": gpu_model,
        "gpu_vram_gb": max(0, int(gpu_vram_gb)),
    }


def _int_env(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return int(default)
    try:
        return int(float(raw))
    except Exception:
        return int(default)


def _detect_ram_gb() -> int:
    if os.name == "nt":
        gb = _ram_gb_windows()
        if gb > 0:
            return gb
    gb = _ram_gb_posix()
    if gb > 0:
        return gb
    return 16


def _ram_gb_windows() -> int:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        if not ok:
            return 0
        return int(stat.ullTotalPhys / (1024**3))
    except Exception:
        return 0


def _ram_gb_posix() -> int:
    try:
        pagesize = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        total = int(pagesize) * int(pages)
        return int(total / (1024**3))
    except Exception:
        return 0
