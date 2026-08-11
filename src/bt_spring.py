"""'이탈 후 회복(스프링)' 검증 — 조하쌤 3번째 룰.

정의 (일봉으로 옮김, 신호일 데이터까지만):
  지지선 = 직전 15일 최저가.
  신호일 D: 장중 저가가 지지선을 0.5% 이상 깨고 내려갔는데, 종가는 지지선 위로 회복 (꼬리).
  진입: D 종가 (회복을 확인하고 들어간다는 뜻) / 손절: D 저가의 -1% ("오늘 저점 재기준")
  목표: +2R / 최대 20일 보유.

대조군 = 같은 날 지지선을 깨고 종가도 못 회복한 '진짜 이탈'. 다음날 이후 성적 비교.
둘의 차이가 "회복 여부"라는 정보의 값어치다.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
from net import RunLog
from replay import load_bars

MIN_VALUE_EOK = 30.0
HOLD = 20


def main():
    log = RunLog()
    bars_all = load_bars()
    log.ok("spring", f"종목 {len(bars_all)}개")

    groups = {"이탈 후 회복 (스프링)": [], "진짜 이탈 (대조군)": []}

    for code, bs in bars_all.items():
        n = len(bs)
        if n < 80:
            continue
        c = [b["close"] for b in bs]
        h = [b["high"] for b in bs]
        lo = [b["low"] for b in bs]
        v = [b["close"] * b["volume"] for b in bs]

        i = 40
        while i < n - HOLD - 1:
            val20 = statistics.mean(v[i - 19:i + 1])
            if val20 / 1e8 < MIN_VALUE_EOK:
                i += 1
                continue
            support = min(lo[i - 15:i])
            if support <= 0:
                i += 1
                continue
            broke = lo[i] <= support * 0.995
            if not broke:
                i += 1
                continue
            recovered = c[i] > support

            entry = c[i]
            stop = lo[i] * 0.99
            if stop >= entry:
                i += 1
                continue
            risk = entry - stop
            target = entry + 2 * risk

            r = None
            for k in range(i + 1, min(i + 1 + HOLD, n)):
                if lo[k] <= stop:
                    r = -1.0
                    break
                if h[k] >= target:
                    r = 2.0
                    break
            if r is None:
                r = (c[min(i + HOLD, n - 1)] - entry) / risk

            key = "이탈 후 회복 (스프링)" if recovered else "진짜 이탈 (대조군)"
            groups[key].append(r)
            i += 5   # 같은 바닥 중복 방지

    print("\n" + "=" * 72)
    print("지지선 이탈 후 회복(스프링) vs 진짜 이탈 — 진입 D종가, 손절 D저가-1%, 목표 2R")
    print("=" * 72)
    for key, rs in groups.items():
        if len(rs) < 100:
            print(f"\n  ▸ {key}: 표본 부족 ({len(rs)})")
            continue
        win = sum(1 for r in rs if r > 0) / len(rs) * 100
        stopped = sum(1 for r in rs if r <= -0.99) / len(rs) * 100
        print(f"\n  ▸ {key}  (n={len(rs):,})")
        print(f"    승률 {win:5.1f}%  평균 {statistics.mean(rs):+.3f}R  "
              f"중앙값 {statistics.median(rs):+.2f}R  손절로 끝남 {stopped:.0f}%")
    print("\n  ※ 수수료 미반영. 진입을 종가로 가정 — 실전(장중 회복 확인 매수)보다 약간 불리/유리 섞임.")


if __name__ == "__main__":
    main()
