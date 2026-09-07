"""초하쌤 3/10 강의 '장기이평(까만선=세력 평단가) 위 정배열 + 이평 대비 ±30% 이내가 적정가' 검증
(2026-09-07, 연구 큐 ⑥, 계약 무관).

캐시가 2년이라 448일선은 최근 며칠밖에 계산이 안 되고 월봉 60선(5년)은 불가 → 224일선(1년)으로 대체 측정.
(오동나무 종배 라이브도 224일선을 손절 기준으로 씀.)

기계화 (사전 고정):
  구간 = 신호일 종가 ÷ 224일 단순이평 − 1: <−30% · −30~−10% · −10~0% · 0~+10% · +10~+30% · >+30%.
  트레이드 세트 (bt_mcap과 동일): (1) A급 자리 + 확인 진입  (2) 무작위(20일마다) — 둘 다 1.5ATR·+2R·20일·거래대금 30억.
  224일선이 있으려면 i ≥ 224 → 표본은 캐시 후반 1년(2025-09~2026-09)만.
비교 기준: A급 +0.117R · 무작위 −0.047R · 8/29 '장기이평 근접 +0.047R(승률 35.9%)'.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import replay
from bt_mcap import COOL, liquid, rsi14, trade

BUCKETS = [(-9, -0.30, "<−30%"), (-0.30, -0.10, "−30~−10%"), (-0.10, 0.0, "−10~0% (선 아래)"),
           (0.0, 0.10, "0~+10% (선 위)"), (0.10, 0.30, "+10~+30%"), (0.30, 9, ">+30% (비쌈)")]


def bucket(x):
    for lo, hi, lab in BUCKETS:
        if lo <= x < hi:
            return lab
    return None


def run(bars_all):
    a_grade, rand = {}, {}
    for code, bars in bars_all.items():
        if len(bars) < 260:
            continue
        cl = [b["close"] for b in bars]
        s224 = sum(cl[:224])
        last = -999
        for i in range(224, len(bars) - 2):
            s224 += cl[i] - cl[i - 224]
            if not liquid(bars, i):
                continue
            lab = bucket(cl[i] / (s224 / 224) - 1)
            if i % 20 == 0:
                r = trade(bars, i)
                if r is not None:
                    rand.setdefault(lab, []).append(r)
            if i - last < COOL:
                continue
            off = cl[i] / max(b["high"] for b in bars[i - 59:i + 1]) - 1
            disp = cl[i] / (sum(cl[i - 19:i + 1]) / 20)
            if -0.15 <= off <= -0.03 and 0.95 <= disp <= 1.20 and 45 <= rsi14(cl, i) <= 75:
                last = i
                r = trade(bars, i)
                if r is not None:
                    a_grade.setdefault(lab, []).append(r)
    return a_grade, rand


def show(title, groups):
    print(title)
    allr = [r for rs in groups.values() for r in rs]
    for _, _, lab in BUCKETS:
        rs = groups.get(lab, [])
        if len(rs) < 30:
            print(f"  {lab:18s}: 표본 {len(rs)} — 부족")
            continue
        win = sum(1 for r in rs if r > 0) / len(rs)
        print(f"  {lab:18s}: n={len(rs):5d} · 평균 {statistics.mean(rs):+.3f}R · 중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%}")
    above = [r for lab, rs in groups.items() for r in rs if lab and "선 위" in lab or (lab and lab.startswith("+"))]
    below = [r for lab, rs in groups.items() for r in rs if lab and (lab.startswith("<") or lab.startswith("−"))]
    if above and below:
        print(f"  {'선 위 전체':18s}: n={len(above):5d} · 평균 {statistics.mean(above):+.3f}R   |   "
              f"선 아래 전체: n={len(below)} · 평균 {statistics.mean(below):+.3f}R")
    if allr:
        print(f"  {'전체':18s}: n={len(allr):5d} · 평균 {statistics.mean(allr):+.3f}R")


def main():
    bars_all = replay.load_bars()
    print(f"캐시 {len(bars_all)}종목 · 224일선 기준 (표본 = 캐시 후반 1년)\n")
    a, r = run(bars_all)
    show("(1) A급 자리 + 확인 진입 — 224일선 대비 구간별", a)
    print()
    show("(2) 무작위 진입(20일마다) — 224일선 대비 구간별", r)
    print("\n비교: A급 +0.117R · 무작위 −0.047R · 초하쌤: 장기선 위 + ±30% 이내 적정, +30% 초과 비쌈 · 수수료 미반영")


if __name__ == "__main__":
    main()
