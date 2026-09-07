"""초하쌤 11/5 강의 '50% 되돌림' 검증 (2026-09-07 사용자 지시, 연구 큐 ④, 계약 무관).

강의 스펙: "바닥에서 올라온 만큼 절반 눌리면 반등이 나오게 돼 있다" (되돌림 50% 룰).

기계화 (사전 고정):
  랠리: 60거래일 저점 L(종가) → 이후 종가 고점 H, H/L ≥ 1.20 (20% 이상 오른 것만), 고점 후 30일 안에 눌림.
  신호: 종가가 H − r×(H−L) 이하로 처음 내려온 날 (r = 되돌림 비율, 기본 45~60% 구간 = '절반'), 종가 > L.
        종목당 냉각 20일. 거래대금 20일 평균 30억 이상.
  진입: (A) 다음날 시가 — 초하 방식(눌림에 산다)  (B) 확인 진입 = 신호일 고가×1.002를 3일 안에 돌파 — 우리 방식
  손절: (a) 진입가 − 1.5ATR14  (b) 랠리 저점 L×0.99 ('절반 눌림이 깨지면 틀린 것')
  목표 +2R · 20거래일 · 손절 우선 · 수수료 미반영.
  깊이 비교: 30~45% · 45~60% · 60~80% (A+a 기준) — '절반'이 특별한지.
비교 기준: A급 +0.117R · 기준봉 눌림(직후) −0.149R · 전체 무작위 −0.047R.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import replay

HOLD, WIN, COOL, VAL_MIN = 20, 3, 20, 30e8
LOOK, RALLY_MIN, PULL_WIN = 60, 1.20, 30


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


def signals(bars, lo_r, hi_r):
    """(신호일 i, 랠리 저점 L) 목록."""
    cl = [b["close"] for b in bars]
    out, last = [], -999
    for i in range(LOOK + 5, len(bars) - 2):
        if i - last < COOL:
            continue
        win = cl[i - LOOK:i]
        b = min(range(len(win)), key=win.__getitem__)          # 60일 저점 위치
        L = win[b]
        seg = win[b:]                                           # 저점 이후 ~ 어제
        hb = max(range(len(seg)), key=seg.__getitem__)
        H = seg[hb]
        if L <= 0 or H / L < RALLY_MIN or hb == 0:
            continue
        days_since_high = len(seg) - 1 - hb
        if days_since_high > PULL_WIN:
            continue
        retr = (H - cl[i]) / (H - L)
        if not (lo_r <= retr <= hi_r) or cl[i] <= L:
            continue
        v20 = sum(x["close"] * x["volume"] for x in bars[i - 19:i + 1]) / 20
        if v20 < VAL_MIN:
            continue
        out.append((i, L))
        last = i
    return out


def run(bars_all, lo_r, hi_r, entry_mode, stop_mode):
    trades, n_sig = [], 0
    for code, bars in bars_all.items():
        if len(bars) < LOOK + 40:
            continue
        for i, L in signals(bars, lo_r, hi_r):
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
            stop = entry - 1.5 * atr14(bars, i) if stop_mode == "atr" else L * 0.99
            r = sim(bars, j, entry, stop)
            if r is not None:
                trades.append(r)
    return trades, n_sig


def agg(rs, n_sig, label):
    if len(rs) < 30:
        print(f"  {label:34s}: 표본 {len(rs)} — 부족")
        return
    win = sum(1 for r in rs if r > 0) / len(rs)
    print(f"  {label:34s}: n={len(rs):5d} (신호 {n_sig:5d}) · 평균 {statistics.mean(rs):+.3f}R · "
          f"중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%}")


def main():
    bars_all = replay.load_bars()
    print(f"캐시 {len(bars_all)}종목 · 랠리 +20%↑ · 고점 후 30일 내 눌림 · 거래대금 30억 · 냉각 20일\n")
    print("① 절반 되돌림(45~60%) — 진입·손절 격자")
    for em, el in (("open", "다음날 시가"), ("trigger", "확인 진입")):
        for sm, sl in (("atr", "1.5ATR"), ("low", "저점×0.99")):
            rs, n = run(bars_all, 0.45, 0.60, em, sm)
            agg(rs, n, f"{el} · 손절 {sl}")
    print("\n② 되돌림 깊이 비교 (다음날 시가 · 1.5ATR)")
    for lo, hi, lab in ((0.30, 0.45, "얕은 30~45%"), (0.45, 0.60, "절반 45~60%"), (0.60, 0.80, "깊은 60~80%")):
        rs, n = run(bars_all, lo, hi, "open", "atr")
        agg(rs, n, lab)
    print("\n③ 되돌림 깊이 비교 (확인 진입 · 1.5ATR)")
    for lo, hi, lab in ((0.30, 0.45, "얕은 30~45%"), (0.45, 0.60, "절반 45~60%"), (0.60, 0.80, "깊은 60~80%")):
        rs, n = run(bars_all, lo, hi, "trigger", "atr")
        agg(rs, n, lab)
    print("\n비교: A급 +0.117R · 기준봉 직후 눌림 −0.149R · 전체 무작위 −0.047R · 수수료 미반영")


if __name__ == "__main__":
    main()
