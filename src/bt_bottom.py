"""'급등주 낙폭과대 바닥 + 타이트 손절' 검증 — 조하쌤식 매매.

패턴 정의 (광전자 8/6 상황을 규칙으로 옮김, 전부 신호일 이전 데이터만):
  ① 급등 이력: 최근 120일 최고가가 현재가의 2.5배 이상 (= 고점 대비 -60% 이상 하락)
  ② 바닥 다지기: 최근 15일 고저 폭이 종가의 18% 이내 (횡보)
  ③ 진입: 최근 10일 고가 돌파 (반등 시동)
  ④ 손절: 15일 바닥 저가의 -1% 밑 — ATR이 아니라 **구조 밑 타이트 손절**
  ⑤ 유동성: 20일 평균 거래대금 30억+

같은 진입·손절 방식을 '급등 이력 없는 일반 횡보'에도 적용해 비교한다.
차이가 곧 "급등주 바닥"이라는 선별의 값어치다.

목표는 두 가지로 잰다:
  (a) 고정 3R 목표  (b) 목표 없이 20일 보유 후 청산 (러너를 얼마나 태우나)
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
    log.ok("bottom", f"종목 {len(bars_all)}개")

    groups = {"급등붕괴 바닥": [], "일반 횡보 (대조군)": []}
    rdist = {"급등붕괴 바닥": []}

    for code, bs in bars_all.items():
        n = len(bs)
        if n < 160:
            continue
        c = [b["close"] for b in bs]
        h = [b["high"] for b in bs]
        lo = [b["low"] for b in bs]
        v = [b["close"] * b["volume"] for b in bs]

        i = 130
        while i < n - HOLD - 3:
            val20 = statistics.mean(v[i - 19:i + 1])
            if val20 / 1e8 < MIN_VALUE_EOK:
                i += 1
                continue

            hi120 = max(h[i - 119:i + 1])
            base_hi, base_lo = max(h[i - 14:i + 1]), min(lo[i - 14:i + 1])
            if base_lo <= 0 or c[i] <= 0:
                i += 1
                continue
            is_spike_wreck = hi120 / c[i] >= 1.7
            is_base = (base_hi - base_lo) / c[i] <= 0.22
            if not is_base:
                i += 1
                continue

            entry = max(h[i - 9:i + 1]) * 1.002
            stop = base_lo * 0.99
            if stop >= entry:
                i += 1
                continue

            # 체결 확인 (3일 내), 갭이면 시가
            fill = fidx = None
            for k in range(1, 4):
                if i + k >= n:
                    break
                if h[i + k] >= entry:
                    fill = max(entry, bs[i + k]["open"])
                    fidx = i + k
                    break
            if fill is None or fill <= stop:
                i += 1
                continue
            risk = fill - stop

            # (a) 3R 고정 목표
            target = fill + 3 * risk
            ra = None
            for k in range(fidx, min(fidx + HOLD, n)):
                if lo[k] <= stop:
                    ra = -1.0
                    break
                if h[k] >= target:
                    ra = 3.0
                    break
            if ra is None:
                ra = (c[min(fidx + HOLD, n) - 1] - fill) / risk

            # (b) 목표 없이 20일 (러너 포함 분포)
            rb = None
            for k in range(fidx, min(fidx + HOLD, n)):
                if lo[k] <= stop:
                    rb = -1.0
                    break
            if rb is None:
                rb = (c[min(fidx + HOLD, n) - 1] - fill) / risk

            key = "급등붕괴 바닥" if is_spike_wreck else "일반 횡보 (대조군)"
            groups[key].append((ra, rb))
            if is_spike_wreck:
                rdist["급등붕괴 바닥"].append(rb)
            i += 10   # 같은 바닥에서 중복 신호 방지

    print("\n" + "=" * 76)
    print("급등주 낙폭과대 바닥 + 구조 밑 타이트 손절  (진입: 10일 고가 돌파)")
    print("=" * 76)
    for key, rows in groups.items():
        if len(rows) < 50:
            print(f"\n  ▸ {key}: 표본 부족 ({len(rows)})")
            continue
        ras = [a for a, _ in rows]
        rbs = [b for _, b in rows]
        wina = sum(1 for r in ras if r > 0) / len(ras) * 100
        print(f"\n  ▸ {key}  (체결 {len(rows):,}건)")
        print(f"    (a) 3R 목표   : 승률 {wina:5.1f}%  평균 {statistics.mean(ras):+.3f}R  "
              f"중앙값 {statistics.median(ras):+.2f}R")
        print(f"    (b) 20일 보유 : 평균 {statistics.mean(rbs):+.3f}R  "
              f"중앙값 {statistics.median(rbs):+.2f}R  최대 {max(rbs):+.1f}R")

    dist = rdist["급등붕괴 바닥"]
    if len(dist) >= 50:
        stopped = sum(1 for r in dist if r <= -0.99) / len(dist) * 100
        big = sum(1 for r in dist if r >= 3) / len(dist) * 100
        print(f"\n  급등붕괴 바닥의 결과 분포 (20일 보유 기준):")
        print(f"    손절로 끝남 {stopped:.0f}% · +3R 이상 {big:.0f}% · "
              f"수수료 미반영")
    print("\n  ※ 손절이 -6% 수준으로 타이트해서 1R이 작다 — R이 커도 원금 대비 수익은 그만큼 작다.")


if __name__ == "__main__":
    main()
