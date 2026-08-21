"""Lightweight pipeline profiling — zero dependencies.

Every run carries a `profile` in its meta: wall time per stage, CPU seconds,
peak RAM, and what geometry actually happened (route, cache hit, segment
count per page). This is the library's own bill of costs — the model's
tokens are someone else's meter.
"""
from __future__ import annotations

import sys
import time
from typing import Optional


def peak_ram_mb() -> Optional[float]:
    """Peak working-set of this process in MB (best effort, cross-platform)."""
    try:
        if sys.platform == "win32":
            import ctypes
            import ctypes.wintypes as wt

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            fn = getattr(ctypes.windll.kernel32, "K32GetProcessMemoryInfo", None)
            if fn is None:
                fn = ctypes.windll.psapi.GetProcessMemoryInfo
            # 64-bit: default int restype/argtypes truncate the pseudo-handle
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wt.DWORD]
            fn.restype = wt.BOOL
            handle = ctypes.c_void_p(-1)  # GetCurrentProcess() pseudo-handle
            if fn(handle, ctypes.byref(pmc), pmc.cb):
                return round(pmc.PeakWorkingSetSize / (1024 * 1024), 1)
        else:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin":  # ru_maxrss is bytes on macOS
                return round(peak / (1024 * 1024), 1)
            return round(peak / 1024, 1)  # kilobytes on Linux
    except Exception:
        pass
    return None


class StageTimer:
    """Collects named wall-clock stages plus total wall/CPU."""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._cpu0 = time.process_time()
        self._mark = self._t0
        self.stages: dict[str, float] = {}

    def stage(self, name: str) -> None:
        now = time.perf_counter()
        self.stages[name] = round(self.stages.get(name, 0) + (now - self._mark), 4)
        self._mark = now

    def finish(self) -> dict:
        return {
            **self.stages,
            "total_s": round(time.perf_counter() - self._t0, 4),
            "cpu_s": round(time.process_time() - self._cpu0, 4),
            "peak_ram_mb": peak_ram_mb(),
        }
