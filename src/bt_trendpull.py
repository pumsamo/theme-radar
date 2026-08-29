"""무지랭이2 10강 '추세선 매매' 검증 — 우상향 종목의 이평선 풀백 매수 (2026-08-30).

초하쌤 스펙 (두산에너빌리티 예시): 우상향 중인 종목은 5일선에 다가오면 매수를 반복,
21선(≈20일선) 종가 이탈 시 절반 정리, 56일선 회복 시 재진입.

기계화 (사전 고정):
  우상향 필터: 종가 > MA20 > MA60 AND MA20이 10일 전보다 위 AND 60일 고점 대비 −15% 이내
  신호: 필터 충족일 다음날 저가가 전일 MA5(또는 MA20 변형) 이하로 내려오면
        min(시가, 그 이평값)에 지정가 체결
  손절: 전일 MA20 × 0.99 (같은 날 목표·손절 겹치면 손절 우선)
  목표: 진입 + 2×(진입−손절). 20거래일 기한 청산. 냉각 5일. 거래대금 30억+.
비교 기준: A급 +0.117R · 좁은박스 +0.104R. 수수료 미반영.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import replay

HOLD, COOL, VAL_MIN = 20, 5, 30e8


def run(bars_all, touch_ma):
    trades = []
    for code, bars in bars_all.items():
        if len(bars) < 90:
            continue
        closes = [b["close"] for b in bars]
        last = -999
        for i in range(70, len(bars) - 1):
            ma5 = sum(closes[i - 4:i + 1]) / 5
            ma20 = sum(closes[i - 19:i + 1]) / 20
            ma60 = sum(closes[i - 59:i + 1]) / 60
            ma20_prev = sum(closes[i - 29:i - 9]) / 20
            hi60 = max(closes[i - 59:i + 1])
            c = closes[i]
            if not (c > ma20 > ma60 and ma20 > ma20_prev and c >= hi60 * 0.85):
                continue
            if i - last < COOL:
                continue
            v20 = sum(b["close"] * b["volume"] for b in bars[i - 19:i + 1]) / 20
            if v20 < VAL_MIN:
                continue
            lvl = ma5 if touch_ma == 5 else ma20
            nxt = bars[i + 1]
            if nxt["low"] > lvl:
                continue  # 이평까지 안 눌림 — 미체결
            entry = min(nxt["open"], lvl)
            stop = ma20 * 0.99
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
    if len(rs) < 20:
        print(f"{label}: 표본 {len(rs)} — 부족")
        return
    win = sum(1 for r in rs if r > 0) / len(rs)
    print(f"{label}: n={len(rs)} · 평균 {statistics.mean(rs):+.3f}R · "
          f"중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%}")


def main():
    bars_all = replay.load_bars()
    print(f"캐시 {len(bars_all)}종목 (우상향 필터: 종가>MA20>MA60 · MA20 상승 · 60일고점 −15% 이내)")
    agg(run(bars_all, 5), "5일선 터치 매수 (초하쌤 원안)")
    agg(run(bars_all, 20), "20일선 터치 매수 (깊은 눌림 변형)")
    print("비교: A급 +0.117R · 좁은박스 돌파 +0.104R · 448 대형 +0.058R · 수수료 미반영")


if __name__ == "__main__":
    main()
