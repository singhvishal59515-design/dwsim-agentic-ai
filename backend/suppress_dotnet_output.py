"""
suppress_dotnet_output.py
─────────────────────────
Suppress .NET-level stdout/stderr on Windows using ctypes to redirect the
Win32 console handle.  Python's redirect_stdout/redirect_stderr does NOT
catch output written directly by the CLR runtime.

Usage:
    from suppress_dotnet_output import suppress_dotnet_console

    with suppress_dotnet_console():
        mgr = Automation()   # ThermoCS warning is now silenced
"""

import os
import sys
import ctypes
from contextlib import contextmanager

# Only meaningful on Windows; on other platforms it's a no-op
_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes.wintypes
    _kernel32   = ctypes.windll.kernel32
    _STD_OUTPUT = ctypes.wintypes.DWORD(-11)  # STD_OUTPUT_HANDLE
    _STD_ERROR  = ctypes.wintypes.DWORD(-12)  # STD_ERROR_HANDLE
    _INVALID    = ctypes.wintypes.HANDLE(-1)


@contextmanager
def suppress_dotnet_console():
    """
    Redirect Win32 STD_OUTPUT and STD_ERROR to NUL for the duration of
    the with-block, then restore them.  Silences .NET CLR console output
    (e.g. the ThermoCS FileNotFoundException spam).
    """
    if not _IS_WINDOWS:
        yield
        return

    # Open NUL (the Windows equivalent of /dev/null)
    nul = ctypes.wintypes.HANDLE(
        _kernel32.CreateFileW(
            "NUL", 0x40000000,  # GENERIC_WRITE
            0x3,                # FILE_SHARE_READ | FILE_SHARE_WRITE
            None, 3,            # OPEN_EXISTING
            0, None
        )
    )

    # Save original handles
    orig_out = ctypes.wintypes.HANDLE(
        _kernel32.GetStdHandle(_STD_OUTPUT)
    )
    orig_err = ctypes.wintypes.HANDLE(
        _kernel32.GetStdHandle(_STD_ERROR)
    )

    # Also flush Python's own buffers first
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    # Redirect to NUL
    _kernel32.SetStdHandle(_STD_OUTPUT, nul)
    _kernel32.SetStdHandle(_STD_ERROR,  nul)

    try:
        yield
    finally:
        # Restore originals
        _kernel32.SetStdHandle(_STD_OUTPUT, orig_out)
        _kernel32.SetStdHandle(_STD_ERROR,  orig_err)
        _kernel32.CloseHandle(nul)