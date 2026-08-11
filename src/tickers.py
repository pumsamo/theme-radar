"""KRX 상장법인 목록 → 종목명 ↔ 종목코드 매핑.

시드 엑셀과 뉴스에는 종목'명'만 있고 시세 조회에는 '코드'가 필요하다.
KIND 상장법인목록(무인증, EUC-KR HTML 테이블)을 하루 1회 받아 캐시한다.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from net import cached, fetch

ALIAS_PATH = Path(__file__).resolve().parent.parent / "config" / "stock_aliases.json"

# searchType=13 = 전 시장 일괄. 컬럼은 [회사명, 시장구분, 종목코드, ...] 순서다.
KIND_URL = "http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
MARKET_LABEL = {"유가": "KOSPI", "코스닥": "KOSDAQ", "코넥스": "KONEX"}

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _clean(html: str) -> str:
    text = _TAG.sub("", html)
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    return text.strip()


def _download() -> dict[str, dict]:
    out: dict[str, dict] = {}
    html = fetch(KIND_URL, timeout=40).decode("euc-kr", errors="replace")
    for row in _ROW.findall(html):
        cells = [_clean(c) for c in _CELL.findall(row)]
        if len(cells) < 3:
            continue
        name, market, code = cells[0], cells[1], cells[2]
        # 코드는 보통 6자리 숫자지만 신주인수권 등은 영문이 섞인다 (예: 0218L0)
        if not re.fullmatch(r"[0-9A-Z]{6}", code):
            continue
        out.setdefault(name, {"code": code,
                              "market": MARKET_LABEL.get(market, market)})
    if len(out) < 1000:
        raise RuntimeError(f"KRX 목록이 비정상적으로 작다 ({len(out)}종목) — 파싱 확인 필요")
    return out


@lru_cache(maxsize=1)
def table() -> dict[str, dict]:
    """{종목명: {code, market}} — 실패해도 예외를 올리지 않고 빈 dict."""
    try:
        return cached("krx_corplist", ttl_sec=20 * 3600, producer=_download)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! [tickers] KRX 목록 수신 실패 — 코드 매핑 없이 진행: {exc}")
        return {}


def _norm(name: str) -> str:
    """비교용 정규화. ★ 같은 수기 표시와 공백·(주)를 털어낸다."""
    s = re.sub(r"^[\s★☆*・·\-]+", "", str(name))
    return re.sub(r"\s+", "", s).replace("(주)", "").strip().lower()


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, str]:
    try:
        return json.loads(ALIAS_PATH.read_text(encoding="utf-8"))["map"]
    except Exception:  # noqa: BLE001 - 별칭 파일이 없어도 동작해야 한다
        return {}


@lru_cache(maxsize=4096)
def to_code(name: str) -> str | None:
    tbl = table()
    if not tbl:
        return None

    alias = _alias_map()
    if name in alias:
        name = alias[name]
        if re.fullmatch(r"[0-9A-Z]{6}", name):
            return name

    if name in tbl:
        return tbl[name]["code"]

    target = _norm(name)
    if not target:
        return None
    for key, val in tbl.items():
        if _norm(key) == target:
            return val["code"]

    # 접미사만 다른 경우(삼화전자 → 삼화전자공업). 후보가 정확히 하나일 때만 인정한다.
    if len(target) >= 4:
        hits = {v["code"] for k, v in tbl.items() if _norm(k).startswith(target)}
        if len(hits) == 1:
            return hits.pop()
    return None


@lru_cache(maxsize=1)
def _by_code() -> dict[str, dict]:
    return {v["code"]: {"name": k, "market": v["market"]} for k, v in table().items()}


def to_name(code: str) -> str | None:
    entry = _by_code().get(code)
    return entry["name"] if entry else None


def market_of(code: str) -> str | None:
    entry = _by_code().get(code)
    return entry["market"] if entry else None


@lru_cache(maxsize=1)
def all_names() -> list[str]:
    """긴 이름부터 — 뉴스 본문에서 종목명 매칭할 때 부분일치 오탐을 줄인다."""
    return sorted(table().keys(), key=len, reverse=True)


if __name__ == "__main__":
    tbl = table()
    print(f"{len(tbl)}종목")
    for n in ("삼성전자", "SK하이닉스", "오이솔루션", "엔젤로보틱스", "위닉스"):
        print(f"  {n} -> {to_code(n)}")
