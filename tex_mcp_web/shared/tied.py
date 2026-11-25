"""Start a process that ends with the process that starts it, however that one ends. A starter
that is ended outright, by SIGKILL or by Windows' TerminateProcess, runs no cleanup, and what it
started would run on: a monitor's waiter that keeps taking presses, or a browser that keeps its
profile.

popen(args, **options) starts *args* as subprocess.Popen(args, **options) does and gives the
Popen. On Linux the process asks the kernel, before it runs the program, to kill it when the
thread that started it exits, and exits at once if its starter has died already. That covers the
process itself, not the processes its program starts in turn. On Windows the process starts
suspended, joins a job object that only the starter holds and that ends what it holds once
closed, and only then runs, so that every process it starts is in the job too. macOS has
neither, and there only the starter's own cleanup ends the process.
"""

from __future__ import annotations

import ctypes
import functools
import os
import signal
import subprocess
import sys

import psutil

if sys.platform.startswith("linux"):
    # Bound before any process starts: the function below runs between fork and exec, where
    # nothing may be loaded or imported.
    _prctl = ctypes.CDLL(None, use_errno=True).prctl
    _PR_SET_PDEATHSIG, _SIGKILL = 1, int(signal.SIGKILL)

    def _tied(starter: int) -> None:
        """In the new process before it runs its program: the kernel kills it once the thread
        that started it has exited, with its process or alone, and one whose starter died
        before this ran exits at once."""
        _prctl(_PR_SET_PDEATHSIG, _SIGKILL)
        if os.getppid() != starter:
            os._exit(1)

elif os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _CREATE_SUSPENDED, _KILL_ON_JOB_CLOSE, _EXTENDED_LIMITS = 0x4, 0x2000, 9
    _PROCESS_SET_QUOTA, _PROCESS_TERMINATE = 0x100, 0x1

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _Limits(ctypes.Structure):
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, its IO_COUNTERS six 64-bit counts.
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    def _checked(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    @functools.cache
    def _job() -> int:
        """The job every process popen() starts is put in, with the processes it starts.
        Only this process holds the job, and Windows ends what is in it once the job's last
        holder has gone, with this process however it ends."""
        job = _checked(_kernel32.CreateJobObjectW(None, None))
        limits = _Limits()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        _checked(_kernel32.SetInformationJobObject(job, _EXTENDED_LIMITS, ctypes.byref(limits),
                                                   ctypes.sizeof(limits)))
        return job

    def _join_job(pid: int) -> None:
        handle = _checked(_kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid))
        try:
            _checked(_kernel32.AssignProcessToJobObject(_job(), handle))
        finally:
            _kernel32.CloseHandle(handle)


def popen(args, **options) -> subprocess.Popen:
    """Start *args* with subprocess.Popen's *options*, to end with this process as the module
    says. On Linux it ends when the calling thread exits, so it is started from a thread that
    lives as long as it is needed, as the main thread does."""
    tie: dict = {}
    if sys.platform.startswith("linux"):
        tie["preexec_fn"] = functools.partial(_tied, os.getpid())
    elif os.name == "nt":
        # Put in the job before it runs, and so before it starts processes of its own.
        tie["creationflags"] = _CREATE_SUSPENDED
    process = subprocess.Popen(args, **options, **tie)
    if os.name == "nt":
        try:
            _join_job(process.pid)
            psutil.Process(process.pid).resume()
        except BaseException:
            process.kill()
            process.wait()
            raise
    return process
