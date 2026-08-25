"""저녁 A급 스캔 트랙 — 그날 종가 기준 지도 내 A급 자리를 DB에 기록한다.

사용자 제안(2026-08-25): "저녁에 물어본 종목들이 실제로 어떻게 됐는지 쌓아보자."
아침 픽(테마 신호 필수)과 다른 그림자 트랙이다. tier='escan'으로 저장해
계약 채점(tier='pick')·자리 완성 표시(tier='watch')와 완전히 분리한다.
채점은 score_evenscan.py — 스윙 10거래일 기준.
"""
from __future__ import annotations

import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date

import boot  # noqa: F401
import prices_kr
import screen
from db import connect
from net import RunLog

MIN_VALUE_EOK = 30.0


def scan(day: str, log: RunLog) -> list[dict]:
    day8 = day.replace("-", "")
    with connect() as conn:
        rows = [(r["code"], r["name"], r["themes"]) for r in conn.execute(
            "SELECT code, name, themes FROM stocks WHERE themes IS NOT NULL")]

    def one(row):
        code, name, themes = row
        try:
            bs = prices_kr.fetch_ohlc(code, "20260301", day8)
        except Exception:  # noqa: BLE001
            return None
        if len(bs) < 70 or bs[-1]["date"] != day8:
            return None
        val20 = statistics.mean(b["close"] * b["volume"] for b in bs[-20:]) / 1e8
        if val20 < MIN_VALUE_EOK:
            return None
        ind = prices_kr.indicators(bs)
        plan = screen.make_plan(ind)
        if plan.get("grade") != "A":
            return None
        return {"code": code, "name": name, "theme": themes.split(",")[0],
                "entry": plan["entry"], "stop": plan["stop"],
                "target1": plan["target1"], "target2": plan["target2"],
                "close": bs[-1]["close"], "val": val20,
                "reason": f"고점대비 {ind['from_hi60']:+.1f}% · RSI {ind['rsi']:.0f} · "
                          f"진입까지 {(plan['entry'] / bs[-1]['close'] - 1) * 100:+.1f}%"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [r for r in pool.map(one, rows) if r]
    found.sort(key=lambda r: -r["val"])
    log.ok("escan", f"{day} A급 {len(found)}종목")
    return found


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else _date.today().isoformat()
    log = RunLog()
    found = scan(day, log)
    with connect() as conn:
        for r in found:
            conn.execute(
                """INSERT OR REPLACE INTO candidates
                   (date, code, name, kr_theme, origin, tier, setup,
                    entry, stop, target1, target2, rr, score, reason, data_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (day, r["code"], r["name"], r["theme"], "evenscan", "escan",
                 "저녁 A급 스캔", r["entry"], r["stop"], r["target1"], r["target2"],
                 2.0, r["val"], r["reason"], "ok"))
        conn.commit()
    for r in found:
        print(f"  {r['name']:<12}{r['theme']:<10} 진입 {r['entry']:>9,.0f} / 손절 {r['stop']:>9,.0f}  ({r['reason']})")


if __name__ == "__main__":
    main()
