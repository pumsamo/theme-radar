"""'대장주가 후발주보다 낫다' 검증 (초하쌤 유치원 1강 주장).

미국 테마 +3% 신호일에, 그 테마 소속 중 A급 자리 + 체결된 종목들을
그날 20일 평균 거래대금 순위로 갈라 성적 비교:
  대장 = 그날 체결된 테마 동료 중 거래대금 1위 / 후발 = 나머지.
같은 날·같은 테마 안에서의 상대 비교라 지도 lookahead 오염이 양쪽에 동일하게 걸린다.
규칙: 고가 돌파 진입(3일) · 1.5ATR 손절 · 2R 목표 · 20일 · 동시터치 손절 우선.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
from net import RunLog
from replay import load_bars
from backtest import us_theme_history
from db import connect

MIN_VALUE_EOK, HOLD = 30.0, 20


def main():
    log = RunLog()
    bars_all = load_bars()
    us = us_theme_history(log)   # {theme: {yyyymmdd: pct}}
    with connect() as conn:
        members = {}
        for r in conn.execute("SELECT code, name, themes FROM stocks WHERE themes IS NOT NULL"):
            for t in (x.strip() for x in r["themes"].split(",")):
                members.setdefault(t, []).append(r["code"])

    import prices_kr
    feats = {}
    def feat(code):
        if code in feats:
            return feats[code]
        bs = bars_all.get(code)
        feats[code] = bs
        return bs

    groups = {"대장(거래대금 1위)": [], "2~3위": [], "4위 이하": []}
    for theme, series in us.items():
        codes = members.get(theme, [])
        if len(codes) < 3:
            continue
        for day, pct in series.items():
            if pct < 3.0:
                continue
            cands = []
            for code in codes:
                bs = feat(code)
                if not bs:
                    continue
                idx = next((i for i, b in enumerate(bs) if b["date"] > day), None)
                i = (idx or 0) - 1
                if idx is None or i < 65 or i + HOLD + 4 >= len(bs):
                    continue
                c = [b["close"] for b in bs]; h = [b["high"] for b in bs]
                lo = [b["low"] for b in bs]; v = [b["close"] * b["volume"] for b in bs]
                val20 = statistics.mean(v[i - 19:i + 1]) / 1e8
                if val20 < MIN_VALUE_EOK:
                    continue
                hi60 = max(h[i - 59:i + 1]); ma20 = sum(c[i - 19:i + 1]) / 20
                fh = (c[i] / hi60 - 1) * 100; ext = c[i] / ma20
                g = [max(c[k] - c[k - 1], 0) for k in range(i - 13, i + 1)]
                l = [max(c[k - 1] - c[k], 0) for k in range(i - 13, i + 1)]
                rsi = 100 - 100 / (1 + sum(g) / sum(l)) if sum(l) else 100
                if not (-15 <= fh <= -3 and 0.95 <= ext <= 1.20 and 45 <= rsi <= 75):
                    continue
                tr = [max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1]))
                      for k in range(i - 13, i + 1)]
                atr = sum(tr) / 14
                entry = h[i] * 1.002
                fill = fidx = None
                for k in range(1, 4):
                    if i + k >= len(bs):
                        break
                    if h[i + k] >= entry:
                        fill, fidx = max(entry, bs[i + k]["open"]), i + k
                        break
                if fill is None:
                    continue
                stop = fill - 1.5 * atr
                if stop >= fill:
                    continue
                target = fill + 2 * (fill - stop)
                r = None
                for k in range(fidx, min(fidx + HOLD, len(bs))):
                    if lo[k] <= stop:
                        r = -1.0; break
                    if h[k] >= target:
                        r = 2.0; break
                if r is None:
                    r = (c[min(fidx + HOLD, len(bs)) - 1] - fill) / (fill - stop)
                cands.append((val20, r))
            if len(cands) < 2:
                continue
            cands.sort(key=lambda x: -x[0])
            groups["대장(거래대금 1위)"].append(cands[0][1])
            for _, r in cands[1:3]:
                groups["2~3위"].append(r)
            for _, r in cands[3:]:
                groups["4위 이하"].append(r)

    print("\n" + "=" * 70)
    print("테마 신호일(美 +3%↑) · A급 체결 종목의 거래대금 순위별 성적 (2년)")
    print("=" * 70)
    for k, vals in groups.items():
        if len(vals) < 30:
            print(f"  {k:<16} 표본 부족({len(vals)})"); continue
        win = sum(1 for r in vals if r > 0) / len(vals) * 100
        print(f"  {k:<16} n={len(vals):>5,}  승률 {win:5.1f}%  평균 {statistics.mean(vals):+.3f}R  중앙값 {statistics.median(vals):+.2f}R")


if __name__ == "__main__":
    main()
