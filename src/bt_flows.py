"""수급(외국인·기관) 소급 검증 — '세력' 가설 2건 (2026-08-30 사전 등록).

데이터: data/flows/{code}.json (collect_flows.py, 최근 1년) × OHLC 캐시.

가설 ① 매집설 조건부 부활: 바닥권(252일 저가 +30% 이내) 거래량 5배 폭발일을
  '기관+외인 합산 순매수(주식수) > 0'와 '< 0'으로 갈랐을 때 이후 성적이 갈리는가.
  bt_accum에서 무조건부는 기각됐다 — 수급이 매집/투매를 판별하는지가 질문.

가설 ② 동반 순매수 지속: 최근 5일 중 기관·외인 '둘 다' 순매수인 날이 4일 이상
  (합산 순매수금액 일평균 1억+) → 이후 성적 vs 기준선.

측정: 신호 다음날 시가 → 20/60거래일 후 수익률. 냉각 20일. 손절 없는 예측력 측정.
기준선: 같은 종목군 무작위 일자. 수수료 미반영.
"""
from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

import boot  # noqa: F401
import replay

FLOWS = Path(__file__).resolve().parent.parent / "data" / "flows"
random.seed(20260830)


def fwd(bars, i, n):
    j = min(i + 1 + n, len(bars) - 1)
    if j <= i + 1 or bars[i + 1]["open"] <= 0:
        return None
    return (bars[j]["close"] / bars[i + 1]["open"] - 1) * 100


def main():
    bars_all = replay.load_bars()
    buckets = {k: {20: [], 60: []} for k in
               ("바닥폭발+순매수", "바닥폭발+순매도", "동반매수지속", "기준선")}
    n_codes = 0

    for path in FLOWS.glob("*.json"):
        code = path.stem
        bars = bars_all.get(code)
        if not bars or len(bars) < 300:
            continue
        try:
            fl = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if len(fl) < 120:
            continue
        n_codes += 1
        idx = {b["date"]: i for i, b in enumerate(bars)}
        last1 = last2 = -999

        for d, (inst, forgn, close, vol, _) in sorted(fl.items()):
            i = idx.get(d)
            if i is None or i < 260 or i >= len(bars) - 21:
                continue
            b = bars[i]
            # ① 바닥권 거래량 폭발 — bt_accum과 동일 조건
            av = sum(x["volume"] for x in bars[i - 20:i]) / 20
            lo252 = min(x["close"] for x in bars[i - 252:i])
            if (av > 0 and b["volume"] >= av * 5 and b["close"] * b["volume"] >= 5e8
                    and b["close"] / lo252 - 1 <= 0.30 and i - last1 >= 20):
                last1 = i
                key = "바닥폭발+순매수" if inst + forgn > 0 else "바닥폭발+순매도"
                for n in (20, 60):
                    r = fwd(bars, i, n)
                    if r is not None:
                        buckets[key][n].append(r)
            # ② 최근 5일 중 4일 이상 기관·외인 동반 순매수
            win = []
            for k in range(5):
                j = i - k
                dj = bars[j]["date"] if j >= 0 else None
                if dj in fl:
                    win.append(fl[dj])
            if len(win) == 5 and i - last2 >= 20:
                both = sum(1 for w in win if w[0] > 0 and w[1] > 0)
                avg_val = sum((w[0] + w[1]) * w[2] for w in win) / 5
                if both >= 4 and avg_val >= 1e8:
                    last2 = i
                    for n in (20, 60):
                        r = fwd(bars, i, n)
                        if r is not None:
                            buckets["동반매수지속"][n].append(r)
        # 기준선
        for _ in range(3):
            i = random.randrange(260, len(bars) - 21)
            if bars[i]["date"] in fl:
                for n in (20, 60):
                    r = fwd(bars, i, n)
                    if r is not None:
                        buckets["기준선"][n].append(r)

    print(f"수급 파일 사용 {n_codes}종목")
    for key, d in buckets.items():
        print(f"\n{key}")
        for n in (20, 60):
            rs = d[n]
            if len(rs) < 30:
                print(f"  {n}일 후: 표본 {len(rs)} — 부족")
                continue
            win = sum(1 for r in rs if r > 0) / len(rs)
            print(f"  {n:>2}일 후: n={len(rs):,} · 평균 {statistics.mean(rs):+.2f}% · "
                  f"중앙값 {statistics.median(rs):+.2f}% · 상승확률 {win:.1%}")


if __name__ == "__main__":
    main()
