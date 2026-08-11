"""HTTP 유틸 + 실행 로그.

지시문 1번/7번: 한 종목·한 소스가 실패해도 사이클 전체를 죽이지 않는다.
실패는 조용히 넘기지 않고 RunLog에 사유를 남겨 리포트 하단에 노출한다.
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import boot  # noqa: F401 - import 시점에 콘솔 인코딩 고정

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


@dataclass
class RunLog:
    """수집 사이클 동안 쌓인 경고/실패. 죽지 않고 계속 가되 기록은 남긴다."""
    notes: list[str] = field(default_factory=list)

    def warn(self, stage: str, msg: str) -> None:
        line = f"[{stage}] {msg}"
        self.notes.append(line)
        print("  ! " + line)

    def ok(self, stage: str, msg: str) -> None:
        print(f"  · [{stage}] {msg}")

    def dump(self) -> list[str]:
        return list(self.notes)


def fetch(url: str, *, headers: dict | None = None, timeout: int = 20,
          retries: int = 2, backoff: float = 1.5) -> bytes:
    """GET. 실패 시 재시도 후 예외를 올린다 (호출부에서 종목/소스 단위로 격리)."""
    hdrs = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if headers:
        hdrs.update(headers)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except Exception as exc:  # noqa: BLE001 - 네트워크는 무엇이든 터진다
            last = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"fetch 실패: {url} ({type(last).__name__}: {last})") from last


def fetch_text(url: str, encoding: str = "utf-8", **kw) -> str:
    return fetch(url, **kw).decode(encoding, errors="replace")


def fetch_json(url: str, **kw) -> dict:
    return json.loads(fetch(url, **kw).decode("utf-8", errors="replace"))


def cached(name: str, ttl_sec: int, producer):
    """하루 안에 여러 번 돌려도 같은 소스를 반복 때리지 않도록 디스크 캐시."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl_sec:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 캐시가 깨졌으면 그냥 새로 받는다
            pass
    value = producer()
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value
