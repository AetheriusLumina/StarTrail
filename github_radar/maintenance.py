"""Shared runtime admission; Inno holds the same byte exclusively for maintenance."""
import ctypes
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

class MaintenanceBusyError(OSError):
    """Fail closed before database, task, or browser initialization."""

class _Overlapped(ctypes.Structure):
    _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),
        ('Offset',ctypes.c_ulong),('OffsetHigh',ctypes.c_ulong),('hEvent',ctypes.c_void_p)]

@lru_cache(maxsize=1)
def _kernel():
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p,
        ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p]
    api.CreateFileW.restype=ctypes.c_void_p
    api.LockFileEx.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,
        ctypes.c_ulong,ctypes.c_ulong,ctypes.POINTER(_Overlapped)]
    api.UnlockFileEx.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,
        ctypes.c_ulong,ctypes.POINTER(_Overlapped)]
    api.CloseHandle.argtypes=[ctypes.c_void_p]
    return api

@contextmanager
def runtime_access(install_dir: Path):
    path=Path(install_dir)/'AppFiles/.radar-maintenance.lock'
    try:path.parent.mkdir(parents=True,exist_ok=True)
    except OSError as exc:raise MaintenanceBusyError('无法确认安装维护状态，请检查安装目录权限') from exc
    api=_kernel()
    # Share deletion permits native uninstall to finish without holding UserData open.
    handle=api.CreateFileW(str(path),0xC0000000,7,None,4,0x80,None)
    if handle==ctypes.c_void_p(-1).value:
        raise MaintenanceBusyError('无法确认安装维护状态，请检查安装目录权限')
    overlap=_Overlapped()
    locked=False
    try:
        # Shared, fail immediately. Existing starts retain admission for their full lifetime.
        locked=bool(api.LockFileEx(handle,1,0,1,0,ctypes.byref(overlap)))
        if not locked:
            raise MaintenanceBusyError('StarTrail 正在安装或卸载，请完成后再打开')
        yield
    finally:
        if locked:api.UnlockFileEx(handle,0,1,0,ctypes.byref(overlap))
        api.CloseHandle(handle)
