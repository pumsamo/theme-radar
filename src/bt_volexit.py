"""초하쌤 9/17 강의 '조용히 오르던 종목은 거래량 터지는 날 팔아라' — 청산 규칙 변형 검증 (2026-09-07, 연구 큐 ⑦, 계약 무관).

진입은 우리 표준 A급(60일 고점比 −15~−3 · 이격 0.95~1.20 · RSI 45~75 · 거래대금 30억 · 확인 진입 3일 창 ·
손절 신호일 종가 − 1.5ATR14)으로 고정하고 **청산만** 바꾼다:
  기준     : +2R 목표 · 20거래일 기한 (계약 규칙)
  V1 거래량 : 목표 없음 · 진입 후 거래량 ≥ 20일 평균 × k 인 첫날 종가 청산 · 20일 기한
  V2 병행   : +2R 목표와 거래량 청산 중 먼저 오는 것
  V3 음봉만 : 거래량 폭발일이 음봉(종가<시가)일 때만 청산, 양봉이면 보유 · +2R 병행 · 20일 기한
  k = 2 · 3 · 5. 손절은 모든 변형 동일(1.5ATR, 손절 우선). 수수료 미반영.
비교 기준: A급 +0.117R(2년) — 여기서는 같은 표본 안의 '기준' 행과 비교한다.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import replay
from bt_mcap import COOL, WIN, HOLD, atr14, liquid, rsi14


def entries(bars_all):
    """A급 신호 → (bars, 진입일 j, entry, stop) 목록."""
    out = []
    for code, bars in bars_all.items():
        if len(bars) < 80:
            continue
        cl = [b["close"] for b in bars]
        last = -999
        for i in range(60, len(bars) - 2):
            if i - last < COOL or not liquid(bars, i):
                continue
            off = cl[i] / max(b["high"] for b in bars[i - 59:i + 1]) - 1
            disp = cl[i] / (sum(cl[i - 19:i + 1]) / 20)
            if not (-0.15 <= off <= -0.03 and 0.95 <= disp <= 1.20 and 45 <= rsi14(cl, i) <= 75):
                continue
            last = i
            trig = bars[i]["high"] * 1.002
            stop = cl[i] - 1.5 * atr14(bars, i)
            for k in range(i + 1, min(i + 1 + WIN, len(bars))):
                if bars[k]["high"] >= trig:
                    entry = max(trig, bars[k]["open"])
                    if entry > stop:
                        out.append((bars, k, entry, stop))
                    break
    return out


def simulate(bars, j, entry, stop, mode, k_vol):
    risk = entry - stop
    target = entry + 2 * risk
    vol = [b["volume"] for b in bars]
    for t, b in enumerate(bars[j:j + HOLD]):
        idx = j + t
        if b["low"] <= stop:
            return -1.0, t + 1
        if mode in ("base", "v2", "v3") and b["high"] >= target:
            return 2.0, t + 1
        if mode != "base":
            v20 = sum(vol[idx - 20:idx]) / 20
            spike = v20 > 0 and vol[idx] >= k_vol * v20
            if spike and (mode != "v3" or b["close"] < b["open"]):
                return (b["close"] - entry) / risk, t + 1
    last = bars[min(j + HOLD - 1, len(bars) - 1)]
    return (last["close"] - entry) / risk, HOLD


def agg(res, label):
    rs = [r for r, _ in res]
    days = [d for _, d in res]
    win = sum(1 for r in rs if r > 0) / len(rs)
    print(f"  {label:26s}: n={len(rs):5d} · 평균 {statistics.mean(rs):+.3f}R · 중앙값 {statistics.median(rs):+.2f}R · "
          f"승률 {win:.1%} · 평균 보유 {statistics.mean(days):.1f}일")


def main():
    bars_all = replay.load_bars()
    ents = entries(bars_all)
    print(f"캐시 {len(bars_all)}종목 · A급 체결 {len(ents)}건 (진입·손절 고정, 청산만 변형)\n")
    agg([simulate(*e, "base", 0) for e in ents], "기준 +2R/20일")
    for k in (2, 3, 5):
        print(f"\n거래량 ×{k}")
        agg([simulate(*e, "v1", k) for e in ents], "V1 거래량 청산(목표 없음)")
        agg([simulate(*e, "v2", k) for e in ents], "V2 +2R 병행")
        agg([simulate(*e, "v3", k) for e in ents], "V3 음봉일 때만 + 2R 병행")
    print("\n비교: 같은 표본의 '기준' 행 대비 · 수수료 미반영 · 보유일 짧아지면 회전 이익은 별도")


if __name__ == "__main__":
    main()
