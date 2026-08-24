"""주 1회 지도 구멍 검사 — 전 상장사 A급 스캔 후 '지도 밖' 종목을 보고한다.

용도: 지도(테마 매핑) 확장 후보 발굴. 픽 규칙과 무관 (스캔·보고만).
운영: 매주 금요일 저녁 루틴에서 실행 (2026-08-25 사용자 확정, 주 1회).
발견한 종목 중 업종이 명백한 것만 사람이 확인해 config/map_extra.json에 넣는다.
"""
from __future__ import annotations

import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date

import boot  # noqa: F401
import prices_kr
import screen
import tickers
from db import connect

MIN_VALUE_EOK = 30.0


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else _date.today().isoformat()
    day8 = day.replace("-", "")
    t = tickers.table()
    with connect() as conn:
        inmap = {r["name"] for r in conn.execute(
            "SELECT name FROM stocks WHERE themes IS NOT NULL")}

    def one(item):
        nm, info = item
        try:
            bs = prices_kr.fetch_ohlc(info["code"], "20260301", day8)
        except Exception:  # noqa: BLE001
            return None
        if len(bs) < 70 or bs[-1]["date"] != day8:
            return None
        val20 = statistics.mean(b["close"] * b["volume"] for b in bs[-20:]) / 1e8
        if val20 < MIN_VALUE_EOK:
            return None
        ind = prices_kr.indicators(bs)
        if screen.make_plan(ind).get("grade") != "A":
            return None
        return (nm, val20, nm in inmap)

    with ThreadPoolExecutor(max_workers=10) as pool:
        res = [r for r in pool.map(one, t.items()) if r]
    outm = sorted((r for r in res if not r[2]), key=lambda r: -r[1])
    print(f"[{day}] 전체 A급 {len(res)} = 지도 안 {sum(1 for r in res if r[2])} + 지도 밖 {len(outm)}")
    print("지도 밖 A급 (거래대금순) — 업종 명백한 놈만 map_extra 후보로:")
    for nm, v, _ in outm[:25]:
        print(f"  {nm:<16} {v:,.0f}억")


if __name__ == "__main__":
    main()
