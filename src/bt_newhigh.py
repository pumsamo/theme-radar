"""초하쌤 8/27 강의 'N개월 종가 신고가 돌파' 검증 (2026-09-07 사용자 지시, 계약 무관 연구).

강의 스펙: 최근 6개월(변형 3개월) 종가 고점을 오늘 종가가 넘으면 매수 후보. 박스 조건 없음 —
bt_box.py(8/28)는 같은 돌파에 '횡보 박스(폭<50%/30%)' 필터를 얹은 것이라, 이번엔 필터 없는
순수 신고가 돌파를 재고 박스 필터·손절 방식·진입 방식을 격자로 비교한다.

기계화 (사전 고정):
  신호: 종가 > 직전 N일 최고 종가 (N=63/126/252). 재신호 냉각 20일. 거래대금 20일 평균 30억 이상.
  진입: (A) 다음날 시가  (B) 확인 진입 = 신호일 고가×1.002를 3일 안에 돌파하면 그 가격(갭이면 시가) — 우리 계약 방식
  손절: (a) 직전 고점(박스 상단)×0.97 — bt_box 방식  (b) 진입가 − 1.5×ATR14 — 우리 계약 방식
  목표 +2R · 20거래일 기한 종가 청산 · 동시 터치는 손절 우선 · 수수료 미반영.
비교 기준: A급 +0.117R · 정배열 돌파 +0.142R · 6개월 좁은박스 +0.104R · 전체 무작위 −0.047R.
"""
from __future__ import annotations

import statistics
import sys

import boot  # noqa: F401
import replay

COOL, HOLD = 20, 20
VAL_MIN = 30e8


def atr14(bars, i):
    trs = []
    for k in range(i - 13, i + 1):
        h, l, pc = bars[k]["high"], bars[k]["low"], bars[k - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def simulate(bars, j, entry, stop):
    """j = 진입일 인덱스(그날 시가부터 보유). R 반환."""
    if entry <= stop:
        return None
    risk = entry - stop
    target = entry + 2 * risk
    for b in bars[j:j + HOLD]:
        if b["low"] <= stop:
            return -1.0
        if b["high"] >= target:
            return 2.0
    return (bars[min(j + HOLD - 1, len(bars) - 1)]["close"] - entry) / risk


def run(bars_all, win, width_max=None, entry_mode="open", stop_mode="top"):
    trades, signals = [], 0
    for code, bars in bars_all.items():
        if len(bars) < win + 40:
            continue
        closes = [b["close"] for b in bars]
        last = -999
        for i in range(max(win, 15), len(bars) - 2):
            box = closes[i - win:i]
            top = max(box)
            if closes[i] <= top or i - last < COOL:
                continue
            if width_max and (min(box) <= 0 or top / min(box) >= width_max):
                continue
            v20 = sum(b["close"] * b["volume"] for b in bars[i - 19:i + 1]) / 20
            if v20 < VAL_MIN:
                continue
            signals += 1
            last = i
            # 진입
            if entry_mode == "open":
                j, entry = i + 1, bars[i + 1]["open"]
            else:  # 확인 진입: 신호일 고가×1.002, 3일 창
                trig = bars[i]["high"] * 1.002
                j = entry = None
                for k in range(i + 1, min(i + 4, len(bars))):
                    if bars[k]["high"] >= trig:
                        j, entry = k, max(trig, bars[k]["open"])
                        break
                if j is None:
                    continue
            stop = top * 0.97 if stop_mode == "top" else entry - 1.5 * atr14(bars, i)
            r = simulate(bars, j, entry, stop)
            if r is not None:
                trades.append(r)
    return trades, signals


def agg(rs, n_sig, label):
    if len(rs) < 30:
        print(f"{label:38s}: 표본 {len(rs)} — 부족")
        return
    win = sum(1 for r in rs if r > 0) / len(rs)
    print(f"{label:38s}: n={len(rs):5d} (신호 {n_sig:5d}) · 평균 {statistics.mean(rs):+.3f}R · "
          f"중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%}")


def main():
    bars_all = replay.load_bars()
    print(f"캐시 {len(bars_all)}종목 · 규칙: +2R/20일/거래대금 30억/냉각 20일\n")
    print("① 순수 신고가 돌파 (박스 필터 없음) — 다음날 시가 진입")
    for win, lab in ((63, "3개월"), (126, "6개월"), (252, "1년")):
        for stop_mode, sl in (("top", "손절 고점×0.97"), ("atr", "손절 1.5ATR")):
            rs, n = run(bars_all, win, None, "open", stop_mode)
            agg(rs, n, f"{lab} 신고가 · {sl}")
    print("\n② 순수 신고가 돌파 — 확인 진입(신호일 고가×1.002, 3일 창)")
    for win, lab in ((63, "3개월"), (126, "6개월"), (252, "1년")):
        rs, n = run(bars_all, win, None, "confirm", "atr")
        agg(rs, n, f"{lab} 신고가 · 확인 진입 · 1.5ATR")
    print("\n③ 박스 필터 비교 (6개월, 다음날 시가, 손절 고점×0.97) — bt_box 재현")
    for wm, lab in ((None, "필터 없음"), (1.5, "폭<50%"), (1.3, "폭<30%")):
        rs, n = run(bars_all, 126, wm, "open", "top")
        agg(rs, n, f"6개월 · {lab}")
    print("\n비교: A급 +0.117R · 정배열 돌파 +0.142R · 6개월 좁은박스 +0.104R · 전체 −0.047R · 수수료 미반영")


if __name__ == "__main__":
    main()
