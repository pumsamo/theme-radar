"""위치별 성적 지도 — "어디서 사는 게 나은가"를 구간별로 나눠 잰다.

기존 backtest.py는 내가 정한 두 셋업(정배열 돌파·눌림목)만 봤다. 그러면 셋업을 잘못
잡았을 때 그 사실을 알 수가 없다. 여기서는 셋업을 아예 없애고, **모든 자리에 똑같은
진입·손절 규칙**을 적용한 뒤 위치(배열·이격도·고점대비 낙폭)로만 갈라서 성적을 비교한다.

공통 규칙 (모든 구간 동일)
  진입: 신호일 고가 돌파 (3거래일 안에 안 닿으면 미체결로 버림)
  체결: 갭 상승이면 시가로 (불리한 쪽)
  손절: 신호일 종가 − 1.5 × ATR14   ← 위치와 무관한 고정 리스크 단위
  목표: 진입 + 2R
  보유: 최대 20거래일, 같은 봉에서 손절·목표 동시 터치 시 손절 우선
  유동성: 20일 평균 거래대금 30억 이상만
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict

import boot  # noqa: F401
from net import CACHE_DIR, RunLog

HOLD, ENTRY_WIN = 20, 3
MIN_VALUE_EOK = 30.0
R_TARGET = 2.0
ATR_MULT = 1.5


def load(limit: int | None) -> dict[str, list[dict]]:
    out = {}
    for p in sorted((CACHE_DIR / "ohlc").glob("*.json")):
        try:
            bars = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if len(bars) > 120:
            out[p.name.split("_")[0]] = bars
        if limit and len(out) >= limit:
            break
    return out


def features(bars: list[dict]):
    """롤링 지표를 한 번에 계산 (매 봉마다 재계산하면 너무 느리다)."""
    n = len(bars)
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    lo = [b["low"] for b in bars]
    v = [b["volume"] for b in bars]

    def sma(arr, w):
        out = [None] * n
        s = 0.0
        for i, x in enumerate(arr):
            s += x
            if i >= w:
                s -= arr[i - w]
            if i >= w - 1:
                out[i] = s / w
        return out

    ma5, ma20, ma60 = sma(c, 5), sma(c, 20), sma(c, 60)
    val = [c[i] * v[i] for i in range(n)]
    val20 = sma(val, 20)

    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
    atr = sma(tr, 14)

    hi60 = [None] * n
    for i in range(n):
        if i >= 59:
            hi60[i] = max(h[i - 59:i + 1])

    # RSI(14) — Wilder 평활
    rsi = [None] * n
    if n > 15:
        gains = [max(0.0, c[i] - c[i - 1]) for i in range(1, n)]
        losses = [max(0.0, c[i - 1] - c[i]) for i in range(1, n)]
        ag = sum(gains[:14]) / 14
        al = sum(losses[:14]) / 14
        rsi[14] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
        for i in range(15, n):
            ag = (ag * 13 + gains[i - 1]) / 14
            al = (al * 13 + losses[i - 1]) / 14
            rsi[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

    # 볼린저밴드(20, 2σ) → %B = (종가-하단)/(상단-하단). 0 미만이면 하단 이탈.
    pctb = [None] * n
    s = s2 = 0.0
    for i in range(n):
        s += c[i]
        s2 += c[i] * c[i]
        if i >= 20:
            s -= c[i - 20]
            s2 -= c[i - 20] * c[i - 20]
        if i >= 19:
            m = s / 20
            var = max(0.0, s2 / 20 - m * m)
            sd = math.sqrt(var)
            if sd > 0:
                upper, lower = m + 2 * sd, m - 2 * sd
                pctb[i] = (c[i] - lower) / (upper - lower)
    return c, h, lo, ma5, ma20, ma60, val20, atr, hi60, rsi, pctb


def simulate(bars, i, entry, stop):
    fwd = bars[i + 1:i + 1 + HOLD + ENTRY_WIN]
    if len(fwd) < HOLD:
        return None
    fill = fidx = None
    for k, b in enumerate(fwd[:ENTRY_WIN]):
        if b["high"] >= entry:
            fill, fidx = max(entry, b["open"]), k
            break
    if fill is None or fill <= stop:
        return None
    risk = fill - stop
    target = fill + R_TARGET * risk
    for b in fwd[fidx:fidx + HOLD]:
        if b["low"] <= stop:
            return -1.0
        if b["high"] >= target:
            return R_TARGET
    return (fwd[fidx + HOLD - 1]["close"] - fill) / risk


def bucket_stats(vals: list[float], base_r: float | None = None) -> str:
    if len(vals) < 100:
        return f"{len(vals):>7,}  표본 부족"
    win = sum(1 for r in vals if r > 0) / len(vals) * 100
    avg = statistics.mean(vals)
    sd = statistics.pstdev(vals) or 1e-9
    se = sd / math.sqrt(len(vals))
    z = avg / se
    tag = "★" if abs(z) > 2.6 else ("·" if abs(z) > 1.65 else " ")
    rel = f"{avg - base_r:+6.3f}" if base_r is not None else "      "
    return f"{len(vals):>7,}  승 {win:5.1f}%  평균 {avg:+6.3f}R  (기준대비 {rel})  z={z:+5.1f} {tag}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    log = RunLog()

    data = load(args.limit)
    log.ok("btpos", f"종목 {len(data)}개")

    by_align: dict[str, list[float]] = defaultdict(list)
    by_ext: dict[str, list[float]] = defaultdict(list)
    by_draw: dict[str, list[float]] = defaultdict(list)
    by_rsi: dict[str, list[float]] = defaultdict(list)
    by_bb: dict[str, list[float]] = defaultdict(list)
    cross: dict[tuple, list[float]] = defaultdict(list)
    cross_rsi: dict[tuple, list[float]] = defaultdict(list)
    allr: list[float] = []

    for code, bars in data.items():
        c, h, lo, ma5, ma20, ma60, val20, atr, hi60, rsi, pctb = features(bars)
        for i in range(60, len(bars) - (HOLD + ENTRY_WIN) - 1):
            if not (ma60[i] and atr[i] and val20[i] and hi60[i]
                    and rsi[i] is not None and pctb[i] is not None):
                continue
            if val20[i] / 1e8 < MIN_VALUE_EOK:
                continue
            entry = h[i] * 1.002
            stop = c[i] - ATR_MULT * atr[i]
            if stop <= 0 or entry <= stop:
                continue
            r = simulate(bars, i, entry, stop)
            if r is None:
                continue
            allr.append(r)

            align = ("정배열" if ma5[i] > ma20[i] > ma60[i]
                     else "역배열" if ma5[i] < ma20[i] < ma60[i] else "혼조")
            ext = c[i] / ma20[i]
            draw = c[i] / hi60[i] - 1

            eb = ("① <0.85 (많이 눌림)" if ext < 0.85 else
                  "② 0.85~0.95" if ext < 0.95 else
                  "③ 0.95~1.05 (20일선 부근)" if ext < 1.05 else
                  "④ 1.05~1.15" if ext < 1.15 else
                  "⑤ 1.15~1.30" if ext < 1.30 else "⑥ >1.30 (과열)")
            db = ("Ⓐ -50% 이하" if draw <= -0.50 else
                  "Ⓑ -50~-30%" if draw <= -0.30 else
                  "Ⓒ -30~-15%" if draw <= -0.15 else
                  "Ⓓ -15~-5%" if draw <= -0.05 else "Ⓔ 고점 -5% 이내")

            rb = ("① RSI<30 (과매도)" if rsi[i] < 30 else
                  "② RSI 30~40" if rsi[i] < 40 else
                  "③ RSI 40~50" if rsi[i] < 50 else
                  "④ RSI 50~60" if rsi[i] < 60 else
                  "⑤ RSI 60~70" if rsi[i] < 70 else "⑥ RSI>70 (과매수)")
            bb = ("Ⓐ %B<0 (하단 이탈)" if pctb[i] < 0 else
                  "Ⓑ %B 0~0.2" if pctb[i] < 0.2 else
                  "Ⓒ %B 0.2~0.5" if pctb[i] < 0.5 else
                  "Ⓓ %B 0.5~0.8" if pctb[i] < 0.8 else
                  "Ⓔ %B 0.8~1.0" if pctb[i] <= 1.0 else "Ⓕ %B>1 (상단 돌파)")

            by_align[align].append(r)
            by_ext[eb].append(r)
            by_draw[db].append(r)
            by_rsi[rb].append(r)
            by_bb[bb].append(r)
            cross[(align, db)].append(r)
            cross_rsi[(align, rb)].append(r)

    base = statistics.mean(allr)
    log.ok("btpos", f"체결 {len(allr):,}건 · 전체 평균 {base:+.3f}R")

    print("\n" + "=" * 88)
    print(f"위치별 성적 지도 — 진입·손절 규칙은 전 구간 동일 (체결 {len(allr):,}건)")
    print("=" * 88)
    print(f"\n  전체 기준선: 평균 {base:+.3f}R · "
          f"승률 {sum(1 for r in allr if r > 0) / len(allr) * 100:.1f}%\n")

    print("  ▸ 이동평균 배열")
    for k in ("정배열", "혼조", "역배열"):
        print(f"    {k:<26}{bucket_stats(by_align[k], base)}")

    print("\n  ▸ 20일선 이격도 (종가 ÷ 20일선) — '너무 오른 걸 사는 게 별로인가'")
    for k in sorted(by_ext):
        print(f"    {k:<26}{bucket_stats(by_ext[k], base)}")

    print("\n  ▸ 60일 고점 대비 낙폭 — '저가면 살 만한가'")
    for k in sorted(by_draw):
        print(f"    {k:<26}{bucket_stats(by_draw[k], base)}")

    print("\n  ▸ RSI(14) — '과매도면 살 만한가'")
    for k in sorted(by_rsi):
        print(f"    {k:<26}{bucket_stats(by_rsi[k], base)}")

    print("\n  ▸ 볼린저밴드 %B (20,2σ) — 0 미만은 하단 이탈, 1 초과는 상단 돌파")
    for k in sorted(by_bb):
        print(f"    {k:<26}{bucket_stats(by_bb[k], base)}")

    print("\n  ▸ 교차: 배열 × RSI (역배열 + 과매도 = 진짜 기회인가)")
    print(f"    {'':<12}{'RSI<30':>13}{'30~40':>13}{'40~50':>13}"
          f"{'50~60':>13}{'60~70':>13}{'>70':>13}")
    for a in ("정배열", "혼조", "역배열"):
        row = f"    {a:<12}"
        for k in ("① RSI<30 (과매도)", "② RSI 30~40", "③ RSI 40~50",
                  "④ RSI 50~60", "⑤ RSI 60~70", "⑥ RSI>70 (과매수)"):
            v = cross_rsi[(a, k)]
            row += (f"{statistics.mean(v):+7.3f}R".rjust(13) if len(v) >= 100
                    else "-".rjust(13))
        print(row)

    print("\n  ▸ 교차: 배열 × 낙폭 (가설 검증 — 역배열이어도 저가면?)")
    print(f"    {'':<12}{'Ⓐ -50%↓':>14}{'Ⓑ -50~-30%':>14}{'Ⓒ -30~-15%':>14}"
          f"{'Ⓓ -15~-5%':>14}{'Ⓔ -5% 이내':>14}")
    for a in ("정배열", "혼조", "역배열"):
        row = f"    {a:<12}"
        for d in ("Ⓐ -50% 이하", "Ⓑ -50~-30%", "Ⓒ -30~-15%", "Ⓓ -15~-5%", "Ⓔ 고점 -5% 이내"):
            v = cross[(a, d)]
            row += (f"{statistics.mean(v):+8.3f}R" + f"({len(v) // 1000}k)").rjust(14) \
                if len(v) >= 100 else "        -     ".rjust(14)
        print(row)

    print("\n  ★ = 기준선과 확실히 다름 · · = 경계 · 공백 = 판단 불가")
    print("  ※ 수수료·세금·슬리피지 미반영. 1R = 1.5×ATR 손절폭.")


if __name__ == "__main__":
    main()
