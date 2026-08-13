"""유튜브 단타·스윙 검색기 레시피 검증.

블로그·유튜브에 도는 대표 조건식 4종을 일봉으로 재현해 2년치에 돌린다.
검색기들의 공통 맹점: **출구(청산) 규칙이 없다.** 그래서 신호의 질 자체를 재기 위해
고정 보유(5/10/20일) 수익률을 전체 시장 평균과 비교한다. 진짜 실력 = 기준선 대비 초과분.

레시피 (원문 조건을 일봉으로 옮김):
  ① 골든크로스     : 5일선이 20일선을 오늘 상향 돌파
  ② 정배열 눌림목   : 5>20>60 정배열 + 오늘 저가가 20일선 터치(±2%)
  ③ 60일 신고가    : 오늘 종가가 60일 최고 종가 갱신
  ④ 단타검색기 대용 : 전일 +10% 이상 급등 + 거래대금 20일 평균의 3배 이상
                     → 다음날 시가 매수, 당일 종가 매도 (검색기 뜬 종목 다음날 단타)

공통: 20일 평균 거래대금 30억+, 진입은 신호 다음날 시가 (현실적 체결).
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
from net import RunLog
from replay import load_bars

MIN_VALUE_EOK = 30.0
HORIZONS = (5, 10, 20)


def sma(arr, i, w):
    return sum(arr[i - w + 1:i + 1]) / w if i >= w - 1 else None


def main():
    log = RunLog()
    bars_all = load_bars()
    log.ok("scr", f"종목 {len(bars_all)}개")

    res = {k: {h: [] for h in HORIZONS} for k in
           ("① 골든크로스", "② 정배열 눌림목", "③ 60일 신고가", "기준선(전체)")}
    danta = []          # ④ 다음날 시가→종가

    for code, bs in bars_all.items():
        n = len(bs)
        if n < 90:
            continue
        c = [b["close"] for b in bs]
        o = [b["open"] for b in bs]
        lo = [b["low"] for b in bs]
        v = [b["close"] * b["volume"] for b in bs]

        for i in range(65, n - 21):
            val20 = statistics.mean(v[i - 19:i + 1])
            if val20 / 1e8 < MIN_VALUE_EOK or o[i + 1] <= 0:
                continue

            entry = o[i + 1]     # 신호 다음날 시가
            fwd = {h: (c[i + h] / entry - 1) * 100 for h in HORIZONS if i + h < n}
            if len(fwd) < len(HORIZONS):
                continue

            for h in HORIZONS:
                res["기준선(전체)"][h].append(fwd[h])

            ma5, ma20, ma60 = sma(c, i, 5), sma(c, i, 20), sma(c, i, 60)
            ma5p, ma20p = sma(c, i - 1, 5), sma(c, i - 1, 20)
            if not all((ma5, ma20, ma60, ma5p, ma20p)):
                continue

            if ma5p <= ma20p and ma5 > ma20:
                for h in HORIZONS:
                    res["① 골든크로스"][h].append(fwd[h])
            if ma5 > ma20 > ma60 and lo[i] <= ma20 * 1.02 and c[i] >= ma20 * 0.98:
                for h in HORIZONS:
                    res["② 정배열 눌림목"][h].append(fwd[h])
            if c[i] >= max(c[i - 59:i + 1]):
                for h in HORIZONS:
                    res["③ 60일 신고가"][h].append(fwd[h])

            # ④ 단타: 전일(i) 급등+대금 폭증 → 다음날 시가→종가
            prev_val = v[i]
            if (c[i] / c[i - 1] - 1) >= 0.10 and prev_val >= 3 * val20:
                danta.append((c[i + 1] / o[i + 1] - 1) * 100)

    print("\n" + "=" * 74)
    print("유튜브 검색기 레시피 — 2년 검증 (진입: 신호 다음날 시가, 수수료 미반영)")
    print("=" * 74)
    print(f"\n  {'레시피':<16}{'표본':>9}{'5일':>9}{'10일':>9}{'20일':>9}   (보유 후 수익률 평균)")
    print("  " + "-" * 62)
    base = {h: statistics.mean(res["기준선(전체)"][h]) for h in HORIZONS}
    for name in ("① 골든크로스", "② 정배열 눌림목", "③ 60일 신고가", "기준선(전체)"):
        d = res[name]
        if len(d[5]) < 200:
            print(f"  {name:<16} 표본 부족({len(d[5])})")
            continue
        cells = "".join(f"{statistics.mean(d[h]):>+8.2f}%" for h in HORIZONS)
        print(f"  {name:<16}{len(d[5]):>9,}{cells}")
    print("\n  기준선 대비 초과분:")
    for name in ("① 골든크로스", "② 정배열 눌림목", "③ 60일 신고가"):
        d = res[name]
        if len(d[5]) < 200:
            continue
        cells = "".join(f"{statistics.mean(d[h]) - base[h]:>+8.2f}%p" for h in HORIZONS)
        print(f"  {name:<16}{'':>9}{cells}")

    if len(danta) >= 200:
        win = sum(1 for r in danta if r > 0) / len(danta) * 100
        print(f"\n  ④ 단타검색기 대용 (전일 급등+대금폭증 → 다음날 시가매수·종가매도)")
        print(f"     표본 {len(danta):,} · 승률 {win:.1f}% · 평균 {statistics.mean(danta):+.2f}% · "
              f"중앙값 {statistics.median(danta):+.2f}%  ← 왕복비용 0.25%는 여기서 또 빠짐")


if __name__ == "__main__":
    main()
