"""초하쌤 8강 '기준봉 눌림' 매수 자리 검증.

기준봉: 하루 +15% 이상(시총 큰 종목은 +10%) 양봉 + 거래량이 20일 평균의 5배 이상.
눌림 진입: 기준봉 이후 3~15일 안에 종가가 기준봉 시가의 -3%~+5% 구간에 들어오고,
          그날 거래량이 기준봉 거래량의 30% 이하(조용히 눌림), 그날 양봉.
진입: 다음날 시가.  손절: 기준봉 시가 × 0.97 ("시가 깨면 손절").  목표 +2R, 20일.
변형: (b) 눌림 확인 후 전일 고가 돌파 시에만 진입.
대조: 같은 기준봉이 나왔는데 눌림 조건 없이 기준봉 다음날 시가에 바로 사기(초보자 행동).
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
from net import RunLog
from replay import load_bars

MIN_VALUE_EOK, HOLD, R_TARGET = 30.0, 20, 2.0


def outcome(bs, fidx, fill, stop):
    if fidx + HOLD > len(bs) or fill <= stop:
        return None
    risk = fill - stop
    target = fill + R_TARGET * risk
    for k in range(fidx, fidx + HOLD):
        if bs[k]["low"] <= stop:
            return -1.0
        if bs[k]["high"] >= target:
            return R_TARGET
    return (bs[fidx + HOLD - 1]["close"] - fill) / risk


def main():
    log = RunLog()
    bars_all = load_bars()
    res = {"기준봉 눌림 → 다음날 시가": [], "기준봉 눌림 → 돌파 시": [], "대조: 기준봉 다음날 바로 매수": []}
    current = []
    for code, bs in bars_all.items():
        n = len(bs)
        if n < 60:
            continue
        c = [b["close"] for b in bs]; o = [b["open"] for b in bs]
        h = [b["high"] for b in bs]; vol = [b["volume"] for b in bs]
        val = [b["close"] * b["volume"] for b in bs]
        i = 25
        while i < n - 1:
            val20 = statistics.mean(val[i - 20:i])
            v20 = statistics.mean(vol[i - 20:i])
            if val20 / 1e8 < MIN_VALUE_EOK or c[i - 1] <= 0 or v20 <= 0:
                i += 1; continue
            chg = c[i] / c[i - 1] - 1
            big = chg >= 0.15 or (chg >= 0.10 and val20 / 1e8 >= 2000)
            if not (big and c[i] > o[i] and vol[i] >= 5 * v20):
                i += 1; continue
            base_open, base_vol = o[i], vol[i]
            stop = base_open * 0.97
            # 대조: 다음날 바로
            if i + 1 < n:
                r = outcome(bs, i + 1, o[i + 1], stop)
                if r is not None:
                    res["대조: 기준봉 다음날 바로 매수"].append(r)
            # 눌림 탐색
            for k in range(i + 3, min(i + 16, n)):
                if 0.97 <= c[k] / base_open <= 1.05 and vol[k] <= 0.3 * base_vol and c[k] > o[k]:
                    if k + 1 < n:
                        r = outcome(bs, k + 1, o[k + 1], stop)
                        if r is not None:
                            res["기준봉 눌림 → 다음날 시가"].append(r)
                        # 돌파 변형
                        entry = h[k] * 1.002
                        for m in range(1, 4):
                            if k + m >= n: break
                            if h[k + m] >= entry:
                                r2 = outcome(bs, k + m, max(entry, o[k + m]), stop)
                                if r2 is not None:
                                    res["기준봉 눌림 → 돌파 시"].append(r2)
                                break
                    if k >= n - 3:
                        current.append((code, bs[i]["date"], base_open, c[k], bs[k]["date"]))
                    break
            i += 1
    print("\n" + "=" * 72)
    print("초하쌤 8강 기준봉 눌림 — 2년 검증 (손절 기준봉시가×0.97 · 2R · 20일 · 수수료 미반영)")
    print("=" * 72)
    for k, vals in res.items():
        if len(vals) < 30:
            print(f"  {k:<28} 표본 부족({len(vals)})"); continue
        win = sum(1 for r in vals if r > 0) / len(vals) * 100
        print(f"  {k:<28} n={len(vals):>5,}  승률 {win:5.1f}%  평균 {statistics.mean(vals):+.3f}R  중앙값 {statistics.median(vals):+.2f}R")
    print(f"\n  ▸ 지금(최근 3거래일) 눌림 조건에 들어온 종목 {len(current)}개:")
    from tickers import to_name
    for code, d, bo, cl, dk in current[:20]:
        print(f"    {to_name(code) or code}({code}) 기준봉 {d[4:6]}/{d[6:]} 시가 {bo:,.0f} → {dk[4:6]}/{dk[6:]} 종가 {cl:,.0f}")


if __name__ == "__main__":
    main()
