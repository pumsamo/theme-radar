"""Windows 콘솔(cp949)에서 한글·기호가 깨지지 않도록 stdout을 UTF-8로 고정."""
from __future__ import annotations

import sys


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 리다이렉트된 스트림 등
            pass


utf8_console()
