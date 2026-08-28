"""무지랭이2 5강 '박스권 안착' 검증 — 6개월 신고 종가 돌파 매수 (2026-08-28).

초하쌤 스펙: 6개월(+) 종가 박스 상단을 종가로 돌파("안착")하면 추세 전환,
박스(횡보)가 길고 좁을수록 크게 간다. 돌파한 상단은 지지로 바뀐다.

기계화 (사전 고정):
  박스: 최근 126거래일 종가 최고/최저 비율 < 1.5 (횡보 필터 — 없으면 상승 추세
        내내 신고가마다 신호가 나와 '박스 돌파'가 아니게 됨)
  신호: 종가가 직전 126일 최고 종가 돌파. 재신호 냉각 20일.
  진입: 다음날 시가. 손절: 박스 상단(직전 최고 종가) × 0.97 — '상단=지지' 규칙.
  목표: +2R. 20거래일 기한 청산. 거래대금 20일 평균 30억 이상. 동시터치 손절 우선.
  변형: 박스폭 <1.3(좁은 박스)과 252일(1년) 박스도 비교 — '길수록 크다' 검증.
비교 기준: A급 +0.117R. 수수료 미반영.
"""
from __future__ import annotations

import statistics
import sys

import boot  # noqa: F401
import replay

WIN, COOL, HOLD = 126, 20, 20
VAL_MIN = 30e8


def run(bars_all, win, width_max):
    trades = []
    for code, bars in bars_all.items():
        if len(bars) < win + 30:
            continue
        closes = [b["close"] for b in bars]
        last = -999
        for i in range(win, len(bars) - 1):
            box = closes[i - win:i]
            top, bot = max(box), min(box)
            if bot <= 0 or top / bot >= width_max:
                continue
            if closes[i] <= top or i - last < COOL:
                continue
            v20 = sum(b["close"] * b["volume"] for b in bars[i - 19:i + 1]) / 20
            if v20 < VAL_MIN:
                continue
            entry = bars[i + 1]["open"]
            stop = top * 0.97
            if entry <= stop:
                continue
            risk = entry - stop
            target = entry + 2 * risk
            r = None
            for b in bars[i + 1:i + 1 + HOLD]:
                if b["low"] <= stop:
                    r = -1.0
                    break
                if b["high"] >= target:
                    r = 2.0
                    break
            if r is None:
                r = (bars[min(i + HOLD, len(bars) - 1)]["close"] - entry) / risk
            trades.append(r)
            last = i
    return trades


def agg(rs, label):
    if len(rs) < 10:
        print(f"{label}: 표본 {len(rs)} — 부족")
        return
    win = sum(1 for r in rs if r > 0) / len(rs)
    print(f"{label}: n={len(rs)} · 평균 {statistics.mean(rs):+.3f}R · "
          f"중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%}")


def main():
    bars_all = replay.load_bars()
    print(f"캐시 {len(bars_all)}종목")
    agg(run(bars_all, 126, 1.5), "6개월 박스(폭<50%) 돌파")
    agg(run(bars_all, 126, 1.3), "6개월 좁은 박스(폭<30%)")
    agg(run(bars_all, 252, 1.5), "1년 박스(폭<50%)")
    agg(run(bars_all, 252, 1.3), "1년 좁은 박스(폭<30%)")
    print("비교: A급 +0.117R · 삼박자 +0.047R · 수수료 미반영")


if __name__ == "__main__":
    main()
