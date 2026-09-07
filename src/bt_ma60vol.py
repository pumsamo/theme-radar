"""초하쌤 11/26 강의 '60일선 부근 거래량 양봉 매수 + 반전 양봉 당일 저가 손절' 검증 (2026-09-07, 연구 큐 ⑤, 계약 무관).

강의 스펙: "하락이 멈추고 60일선 부근까지 올라온 종목에서 평소 대비 거래량이 급증한 양봉이 1개 나오면
그 부근이 매수가", "반등 양봉이 뜬 날의 저가가 깨지면 미련 없이 판다".

기계화 (사전 고정):
  자리: 종가가 60일 이평선의 ±5% 안. 접근 방향은 20일 전 종가가 60일선 아래였으면 '아래서 접근'(강의 상황),
        위였으면 '위에서 눌림'으로 나눠 본다.
  신호: 양봉(종가>시가) + 거래량 ≥ 20일 평균 × k (k=2 기본, 3 변형). 거래대금 20일 평균 30억. 냉각 20일.
  진입: (A) 다음날 시가 — 초하 방식  (B) 확인 진입 = 신호일 고가×1.002를 3일 안에 돌파 — 우리 방식
  손절: (a) 신호일(반전 양봉) 저가 — 초하 방식  (b) 진입가 − 1.5ATR14 — 우리 방식
  목표 +2R · 20거래일 · 손절 우선 · 수수료 미반영.
비교 기준: A급 +0.117R · 전체 무작위 −0.047R · 8/22 '바닥 거래량 폭발'(수급 불문) 중앙값 −3.5%.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import replay

HOLD, WIN, COOL, VAL_MIN, BAND = 20, 3, 20, 30e8, 0.05


def atr14(bars, i):
    trs = []
    for k in range(i - 13, i + 1):
        h, l, pc = bars[k]["high"], bars[k]["low"], bars[k - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def sim(bars, j, entry, stop):
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


def signals(bars, vol_k, approach):
    cl = [b["close"] for b in bars]
    vol = [b["volume"] for b in bars]
    out, last = [], -999
    s60 = sum(cl[:60])
    for i in range(60, len(bars) - 2):
        s60 += cl[i] - cl[i - 60]
        ma60 = s60 / 60
        if i - last < COOL or abs(cl[i] / ma60 - 1) > BAND:
            continue
        ma60_20 = sum(cl[i - 79:i - 19]) / 60
        below_before = cl[i - 20] < ma60_20
        if approach == "below" and not below_before:
            continue
        if approach == "above" and below_before:
            continue
        b = bars[i]
        if b["close"] <= b["open"]:
            continue
        v20 = sum(vol[i - 20:i]) / 20
        if v20 <= 0 or vol[i] < vol_k * v20:
            continue
        if sum(x["close"] * x["volume"] for x in bars[i - 19:i + 1]) / 20 < VAL_MIN:
            continue
        out.append(i)
        last = i
    return out


def run(bars_all, vol_k, approach, entry_mode, stop_mode):
    trades, n_sig = [], 0
    for code, bars in bars_all.items():
        if len(bars) < 100:
            continue
        for i in signals(bars, vol_k, approach):
            n_sig += 1
            if entry_mode == "open":
                j, entry = i + 1, bars[i + 1]["open"]
            else:
                trig = bars[i]["high"] * 1.002
                j = entry = None
                for k in range(i + 1, min(i + 1 + WIN, len(bars))):
                    if bars[k]["high"] >= trig:
                        j, entry = k, max(trig, bars[k]["open"])
                        break
                if j is None:
                    continue
            stop = bars[i]["low"] * 0.999 if stop_mode == "low" else entry - 1.5 * atr14(bars, i)
            r = sim(bars, j, entry, stop)
            if r is not None:
                trades.append(r)
    return trades, n_sig


def agg(rs, n_sig, label):
    if len(rs) < 30:
        print(f"  {label:36s}: 표본 {len(rs)} — 부족")
        return
    win = sum(1 for r in rs if r > 0) / len(rs)
    print(f"  {label:36s}: n={len(rs):5d} (신호 {n_sig:5d}) · 평균 {statistics.mean(rs):+.3f}R · "
          f"중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%}")


def main():
    bars_all = replay.load_bars()
    print(f"캐시 {len(bars_all)}종목 · 60일선 ±5% · 양봉 · 거래대금 30억 · 냉각 20일\n")
    print("① 아래서 60일선으로 올라온 자리 (강의 상황) · 거래량 ×2 — 진입·손절 격자")
    for em, el in (("open", "다음날 시가"), ("trigger", "확인 진입")):
        for sm, sl in (("low", "양봉 저가"), ("atr", "1.5ATR")):
            rs, n = run(bars_all, 2, "below", em, sm)
            agg(rs, n, f"{el} · 손절 {sl}")
    print("\n② 접근 방향·거래량 배수 비교 (다음날 시가 · 양봉 저가 손절)")
    for ap, al in (("below", "아래서 접근"), ("above", "위에서 눌림"), ("any", "방향 무관")):
        for vk in (2, 3):
            rs, n = run(bars_all, vk, ap, "open", "low")
            agg(rs, n, f"{al} · 거래량 ×{vk}")
    print("\n③ 같은 격자, 확인 진입 · 1.5ATR (우리 표준)")
    for ap, al in (("below", "아래서 접근"), ("above", "위에서 눌림"), ("any", "방향 무관")):
        rs, n = run(bars_all, 2, ap, "trigger", "atr")
        agg(rs, n, f"{al} · 거래량 ×2")
    print("\n비교: A급 +0.117R · 전체 무작위 −0.047R · 수수료 미반영")


if __name__ == "__main__":
    main()
