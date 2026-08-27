"""448일선 지지 가설 검증 (초하쌤 + 사용자 8/27 가설: 448선 부근 + 실적 우량 대형주).

규칙 (사전 고정):
  신호: 종가가 MA448 ±3% 이내 AND 최근 60일 내 MA448×1.03 위에 있었던 적 있음
        (위에서 내려와 지지를 테스트하는 경우만 — 만성 하락주 제외)
  진입: 다음날 시가. 손절: MA448×0.97 (같은 날 목표·손절 겹치면 손절 우선)
  목표: 진입가 + 2×(진입가−손절가). 20거래일 초과 시 종가 청산.
  재신호 냉각: 같은 종목 20거래일 내 중복 신호 스킵.
  분할: 신호일 20일 평균 거래대금 중앙값 기준 대형/소형 — '대형주 한정' 가설 검증.
  실적 필터는 소급 불가(과거 시점 재무 스냅샷 필요)라 이번 판은 거래대금을 프록시로 쓴다.

데이터: 네이버 일봉 4년 (20220801~). 수수료 미반영.
"""
from __future__ import annotations

import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

import boot  # noqa: F401
from db import connect
from prices_kr import fetch_ohlc

START, END = "20220801", "20260827"
NEAR = 0.03
HOLD = 20
COOL = 20


def fetch(code):
    try:
        return code, fetch_ohlc(code, START, END)
    except Exception:  # noqa: BLE001
        return code, []


def simulate(bars, i, ma):
    """신호일 i → 다음날 시가 진입. (R, 체결여부)"""
    if i + 1 >= len(bars):
        return None
    entry = bars[i + 1]["open"]
    stop = ma * 0.97
    if entry <= stop:
        return None
    risk = entry - stop
    target = entry + 2 * risk
    for b in bars[i + 1:i + 1 + HOLD]:
        if b["low"] <= stop:
            return -1.0
        if b["high"] >= target:
            return 2.0
    last = bars[min(i + HOLD, len(bars) - 1)]["close"]
    return (last - entry) / risk


def main():
    db = connect()
    codes = {c: n for c, n in db.execute(
        "select code, name from stocks where themes != ''")}
    with ThreadPoolExecutor(8) as ex:
        data = dict(ex.map(fetch, codes))

    trades = []  # (name, date, r, val20)
    for code, bars in data.items():
        if len(bars) < 470:
            continue
        closes = [b["close"] for b in bars]
        last_sig = -999
        for i in range(448, len(bars) - 1):
            ma = sum(closes[i - 447:i + 1]) / 448
            c = closes[i]
            if abs(c / ma - 1) > NEAR or i - last_sig < COOL:
                continue
            if not any(closes[j] > ma * 1.03 for j in range(max(448, i - 60), i)):
                continue
            r = simulate(bars, i, ma)
            if r is None:
                continue
            v20 = sum(b["close"] * b["volume"] for b in bars[i - 19:i + 1]) / 20
            trades.append((codes[code], bars[i]["date"], r, v20))
            last_sig = i

    if not trades:
        print("표본 없음")
        return

    def agg(rows, label):
        rs = [t[2] for t in rows]
        if not rs:
            print(f"{label}: 표본 없음")
            return
        win = sum(1 for r in rs if r > 0) / len(rs)
        print(f"{label}: n={len(rs)} · 평균 {statistics.mean(rs):+.3f}R · "
              f"중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%} · "
              f"목표달성 {sum(1 for r in rs if r >= 2):d} · 손절 {sum(1 for r in rs if r <= -1):d}")

    agg(trades, "전체")
    med_val = statistics.median(t[3] for t in trades)
    agg([t for t in trades if t[3] >= med_val], f"대형(거래대금 상위 1/2, 기준 {med_val/1e8:.0f}억)")
    agg([t for t in trades if t[3] < med_val], "소형(하위 1/2)")
    top_q = sorted(t[3] for t in trades)[int(len(trades) * 0.75)]
    agg([t for t in trades if t[3] >= top_q], f"초대형(상위 1/4, 기준 {top_q/1e8:.0f}억)")
    agg([t for t in trades if t[1] >= "20250801"], "최근 1년만")
    print("\n비교 기준: A급 자리 19,980건 +0.117R · 삼박자 +0.047R · 수수료 미반영")


if __name__ == "__main__":
    main()
