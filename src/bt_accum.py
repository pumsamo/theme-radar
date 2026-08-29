"""초하쌤 세력 매집론 검증 — 바닥권 대량거래는 예고인가 (2026-08-30).

주장 (무지랭이2 8강): 448선 아래·바닥권에서 거래량이 크게 터지는 건 세력 매집 흔적이고,
그런 종목은 이후 크게 간다. 고점(바닥 대비 +50% 이상)에서 터진 거래량은 반대로 차익실현.

검증 (사전 고정):
  매집 신호: 거래량 ≥ 직전 20일 평균 × 5 AND 종가가 252일 저가 대비 +30% 이내(바닥권)
             AND 종가 < 448일선(가능한 경우) — 재신호 냉각 60일. 거래대금 5억+ (동전주 제외).
  분산 신호(대조): 같은 거래량 조건 AND 252일 저가 대비 +80% 이상(고점권).
  측정: 신호 다음날 시가 매수 가정 → 20/60/120거래일 후 수익률.
  기준선: 같은 기간 전 종목 무작위 일자의 같은 보유기간 수익률 중앙값·평균.
매매 시뮬이 아니라 예측력 측정 — 손절 없음. 수수료 미반영.
"""
from __future__ import annotations

import random
import statistics

import boot  # noqa: F401
import replay

VOL_X, COOL = 5.0, 60
random.seed(448)  # 재현성 (Date.now 아님 — 고정 시드)


def fwd(bars, i, n):
    j = min(i + 1 + n, len(bars) - 1)
    if j <= i + 1 or bars[i + 1]["open"] <= 0:
        return None
    return (bars[j]["close"] / bars[i + 1]["open"] - 1) * 100


def main():
    bars_all = replay.load_bars()
    accum, dist, base = {20: [], 60: [], 120: []}, {20: [], 60: [], 120: []}, {20: [], 60: [], 120: []}

    for code, bars in bars_all.items():
        if len(bars) < 300:
            continue
        last_sig = -999
        for i in range(260, len(bars) - 21):
            b = bars[i]
            v20 = [x["volume"] for x in bars[i - 20:i]]
            av = sum(v20) / 20
            if av <= 0 or b["volume"] < av * VOL_X:
                continue
            if b["close"] * b["volume"] < 5e8:
                continue
            lo252 = min(x["close"] for x in bars[i - 252:i])
            pos = b["close"] / lo252 - 1
            ma448 = (sum(x["close"] for x in bars[i - 447:i + 1]) / 448
                     if i >= 447 else None)
            if i - last_sig < COOL:
                continue
            if pos <= 0.30 and (ma448 is None or b["close"] < ma448):
                bucket = accum
            elif pos >= 0.80:
                bucket = dist
            else:
                continue
            last_sig = i
            for n in (20, 60, 120):
                r = fwd(bars, i, n)
                if r is not None:
                    bucket[n].append(r)
        # 기준선: 무작위 5일
        for _ in range(5):
            i = random.randrange(260, len(bars) - 21)
            for n in (20, 60, 120):
                r = fwd(bars, i, n)
                if r is not None:
                    base[n].append(r)

    def show(d, label):
        print(f"\n{label}")
        for n in (20, 60, 120):
            rs = d[n]
            if len(rs) < 30:
                print(f"  {n}일 후: 표본 {len(rs)} — 부족")
                continue
            win = sum(1 for r in rs if r > 0) / len(rs)
            print(f"  {n:>3}일 후: n={len(rs):,} · 평균 {statistics.mean(rs):+.2f}% · "
                  f"중앙값 {statistics.median(rs):+.2f}% · 상승확률 {win:.1%}")

    show(accum, "① 바닥권 대량거래 (매집 후보 — 저가 +30% 이내 · 448 아래)")
    show(dist, "② 고점권 대량거래 (분산 후보 — 저가 +80% 이상)")
    show(base, "③ 기준선 (무작위 일자)")


if __name__ == "__main__":
    main()
