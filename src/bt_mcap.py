"""초하쌤 '시총 하한' 규칙 검증 (2026-09-07 사용자 지시, 연구 큐 ②, 계약 무관).

강의 스펙: "시총 1,000억 미만은 매매도 관심종목도 금지"(11/5·1/21·8/27 세 편 일관), "1,000~5,000억이 빠르게
오르는 스윗스팟, 500억 미만·초대형은 회피"(3/10).

기계화 (사전 고정):
  시총 = 그날 종가 × 현재 상장주식수 (data/mcap.json, collect_mcap.py 스냅샷 — 주식수 변동은 무시한 근사).
  구간: <500억 · 500~1,000억 · 1,000~5,000억 · 5,000억~2조 · 2조 이상.
  트레이드 세트 두 개에 같은 구간을 적용:
    (1) A급 자리: 60일 고점比 −15~−3% · 20일 이격 0.95~1.20 · RSI14 45~75 · 거래대금 20일 평균 30억 · 냉각 20일
        진입 = 신호일 고가×1.002를 3일 안에 돌파(갭이면 시가), 손절 = 신호일 종가 − 1.5ATR14, +2R, 20일 기한.
    (2) 무작위 기준선: 종목마다 20거래일마다 한 번 같은 진입·손절·청산 규칙(자리 조건 없음, 거래대금 30억만).
  → 구간별 평균 R·승률·n. 시총 효과가 자리와 무관하게 존재하는지(2)와 A급 안에서 필터 가치가 있는지(1)를 본다.
비교 기준: A급 전체 +0.117R · 무작위 전체 −0.047R. 수수료 미반영.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import boot  # noqa: F401
import replay

ROOT = Path(__file__).resolve().parent.parent
HOLD, WIN, COOL, VAL_MIN = 20, 3, 20, 30e8
BUCKETS = [(0, 500, "<500억"), (500, 1000, "500~1,000억"), (1000, 5000, "1,000~5,000억"),
           (5000, 20000, "5,000억~2조"), (20000, 1e12, "2조 이상")]


def atr14(bars, i):
    trs = []
    for k in range(i - 13, i + 1):
        h, l, pc = bars[k]["high"], bars[k]["low"], bars[k - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def rsi14(cl, i):
    g = l = 0.0
    for k in range(i - 13, i + 1):
        d = cl[k] - cl[k - 1]
        g += max(d, 0)
        l += max(-d, 0)
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def trade(bars, i):
    """신호일 i → 확인 진입(3일 창) → R. 미체결이면 None."""
    trig = bars[i]["high"] * 1.002
    stop = bars[i]["close"] - 1.5 * atr14(bars, i)
    for k in range(i + 1, min(i + 1 + WIN, len(bars))):
        if bars[k]["high"] >= trig:
            entry = max(trig, bars[k]["open"])
            if entry <= stop:
                return None
            risk = entry - stop
            target = entry + 2 * risk
            for b in bars[k:k + HOLD]:
                if b["low"] <= stop:
                    return -1.0
                if b["high"] >= target:
                    return 2.0
            return (bars[min(k + HOLD - 1, len(bars) - 1)]["close"] - entry) / risk
    return None


def liquid(bars, i):
    return sum(b["close"] * b["volume"] for b in bars[i - 19:i + 1]) / 20 >= VAL_MIN


def bucket(cap_eok):
    for lo, hi, lab in BUCKETS:
        if lo <= cap_eok < hi:
            return lab
    return None


def run(bars_all, shares):
    a_grade, rand = {}, {}
    for code, bars in bars_all.items():
        sh = shares.get(code)
        if not sh or len(bars) < 80:
            continue
        cl = [b["close"] for b in bars]
        last = -999
        for i in range(60, len(bars) - 2):
            if not liquid(bars, i):
                continue
            cap = cl[i] * sh / 1e8
            lab = bucket(cap)
            if i % 20 == 0:                       # 무작위 기준선
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
            print(f"  {lab:14s}: 표본 {len(rs)} — 부족")
            continue
        win = sum(1 for r in rs if r > 0) / len(rs)
        print(f"  {lab:14s}: n={len(rs):5d} · 평균 {statistics.mean(rs):+.3f}R · 중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%}")
    if allr:
        print(f"  {'전체':14s}: n={len(allr):5d} · 평균 {statistics.mean(allr):+.3f}R")


def main():
    mc = json.loads((ROOT / "data" / "mcap.json").read_text(encoding="utf-8"))
    shares = {c: v["shares"] for c, v in mc["stocks"].items()}
    bars_all = replay.load_bars()
    print(f"캐시 {len(bars_all)}종목 · 시총 스냅샷 {mc['asof']} {len(shares)}종목 · 시총 = 종가×현재 주식수(근사)\n")
    a, r = run(bars_all, shares)
    show("(1) A급 자리 + 확인 진입 — 시총 구간별", a)
    print()
    show("(2) 무작위 진입(20일마다) — 시총 구간별", r)
    print("\n비교: A급 전체 +0.117R · 무작위 −0.047R · 초하쌤 규칙: 1,000억 미만 금지, 1,000~5,000억 스윗스팟 · 수수료 미반영")


if __name__ == "__main__":
    main()
