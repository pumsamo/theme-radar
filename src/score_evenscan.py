"""저녁 A급 스캔 트랙 채점 — 스윙 기준 (돌파 진입 3일 · 손절우선 · 2R · 10거래일).

기록일 다음 거래일부터: 진입 = entry 돌파 시(3일 내, 갭이면 시가), 손절 우선,
목표 +2R, 체결 후 10거래일 지나면 종가 청산. 아침 픽 계약과 완전 분리된 참고 트랙.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import prices_kr
from db import connect

HORIZON = 10


def main() -> None:
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM candidates WHERE origin='evenscan' ORDER BY date, name")]
    if not rows:
        print("기록 없음")
        return
    done, open_, pend = [], [], 0
    for p in rows:
        day8 = p["date"].replace("-", "")
        try:
            bs = [b for b in prices_kr.fetch_ohlc(p["code"], day8, "20991231")
                  if b["date"] > day8]
        except Exception:  # noqa: BLE001
            continue
        if not bs:
            pend += 1
            continue
        fill = fidx = None
        for k, b in enumerate(bs[:3]):
            if b["high"] >= p["entry"]:
                fill, fidx = max(p["entry"], b["open"]), k
                break
        if fill is None:
            if len(bs) >= 3:
                done.append((p, "미체결", None))
            else:
                pend += 1
            continue
        risk = fill - p["stop"]
        target = fill + 2 * risk
        r = None
        for k in range(fidx, min(fidx + HORIZON, len(bs))):
            if bs[k]["low"] <= p["stop"]:
                r = -1.0
                break
            if bs[k]["high"] >= target:
                r = 2.0
                break
        if r is None:
            if len(bs) >= fidx + HORIZON:
                r = (bs[fidx + HORIZON - 1]["close"] - fill) / risk
            else:
                open_.append((p, (bs[-1]["close"] - fill) / risk))
                continue
        done.append((p, "종결", r))

    filled = [d for d in done if d[1] == "종결"]
    nofill = sum(1 for d in done if d[1] == "미체결")
    print("\n★ 저녁 A급 스캔 트랙 (스윙 10거래일 · 계약과 무관한 참고 지표)")
    print(f"  기록 {len(rows)}건 → 판정불가 {pend} · 미체결 {nofill} · 보유중 {len(open_)} · 종결 {len(filled)}")
    if filled:
        vals = [r for _, _, r in filled]
        win = sum(1 for v in vals if v > 0) / len(vals) * 100
        print(f"  종결 성적: 승률 {win:.1f}% · 평균 {statistics.mean(vals):+.3f}R · 중앙값 {statistics.median(vals):+.2f}R")
    for p, r in open_:
        print(f"    보유중 {p['date']} {p['name']}: {r:+.2f}R")


if __name__ == "__main__":
    main()
