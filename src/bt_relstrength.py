"""초하쌤 '상대강도' 규칙 2개 검증 (2026-09-07 사용자 지시, 계약 무관 연구).

(A) 3/10 강의: "급락 구간에서 낙폭이 작았던 종목 = 우상향 후보"
(B) 1/21 강의: "핫한 섹터 안에서 기간수익률 역순 정렬 → 덜 오른 종목을 고른다"

기계화 (사전 고정):
  A. 시장 급락 이벤트 = 코스피 5거래일 수익률 ≤ −4% (연속 이벤트 냉각 10일). 이벤트일 t에 거래대금 20일 평균
     30억 이상 종목을 같은 5일 수익률로 5분위 → 상위 20%(덜 빠짐·올랐음) vs 하위 20%(많이 빠짐) vs 전체.
  B. 핫테마 이벤트 = 지도 테마(구성 5종목 이상) 구성종목 5일 수익률 중앙값 ≥ +8% (테마별 냉각 10일).
     이벤트일에 구성종목을 5일 수익률로 정렬 → 하위 1/3(지각주) vs 상위 1/3(대장·주도주).
     지도는 현재 지도(2026-07~08 급등으로 만들어져 룩어헤드 있음) — 두 레그가 같은 편향을 공유하므로
     '지각 vs 대장' 상대 비교만 신뢰하고 절대 수치는 믿지 않는다.
  거래 규칙(우리 표준): 다음날 시가 진입 · 손절 1.5ATR14 · 목표 +2R · 20거래일 기한 · 손절 우선 · 수수료 미반영.
  병기: 무손절 20거래일 단순 보유 수익률(순수 방향 효과) — 진입 다음날 시가 → 20일 뒤 종가.
비교 기준: A급 +0.117R · 전체 무작위 −0.047R.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

import boot  # noqa: F401
import replay
from net import RunLog, fetch_json
from score_day import theme_members

KOSPI = "https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11?range=3y&interval=1d"
HOLD, COOL, VAL_MIN = 20, 10, 30e8
CRASH, HOT = -0.04, 0.08


def atr14(bars, i):
    trs = []
    for k in range(i - 13, i + 1):
        h, l, pc = bars[k]["high"], bars[k]["low"], bars[k - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def trade(bars, i):
    """i = 신호일. 다음날 시가 진입. (R, 무손절 20일 수익률) 반환."""
    j = i + 1
    if j + HOLD >= len(bars) or i < 20:
        return None
    entry = bars[j]["open"]
    stop = entry - 1.5 * atr14(bars, i)
    if entry <= stop or entry <= 0:
        return None
    risk = entry - stop
    target = entry + 2 * risk
    r = None
    for b in bars[j:j + HOLD]:
        if b["low"] <= stop:
            r = -1.0
            break
        if b["high"] >= target:
            r = 2.0
            break
    if r is None:
        r = (bars[j + HOLD - 1]["close"] - entry) / risk
    raw = bars[j + HOLD - 1]["close"] / entry - 1
    return r, raw


def liquid(bars, i):
    return sum(b["close"] * b["volume"] for b in bars[i - 19:i + 1]) / 20 >= VAL_MIN


def agg(rows, label):
    if len(rows) < 30:
        print(f"  {label:26s}: 표본 {len(rows)} — 부족")
        return
    rs = [r for r, _ in rows]
    raws = [x for _, x in rows]
    win = sum(1 for r in rs if r > 0) / len(rs)
    print(f"  {label:26s}: n={len(rs):5d} · 평균 {statistics.mean(rs):+.3f}R · 중앙값 {statistics.median(rs):+.2f}R · "
          f"승률 {win:.1%} · 무손절 20일 {statistics.mean(raws):+.2%}")


def main():
    log = RunLog()
    bars_all = replay.load_bars()
    idx = {c: {b["date"]: k for k, b in enumerate(bs)} for c, bs in bars_all.items()}
    data = fetch_json(KOSPI, timeout=25)["chart"]["result"][0]
    kd = [(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y%m%d"), c)
          for t, c in zip(data["timestamp"], data["indicators"]["quote"][0]["close"]) if c]
    print(f"캐시 {len(bars_all)}종목 · 코스피 {len(kd)}일\n")

    # ---------- A. 시장 급락 후 상대강도 ----------
    events, last = [], -99
    for k in range(5, len(kd)):
        r5 = kd[k][1] / kd[k - 5][1] - 1
        if r5 <= CRASH and k - last >= COOL:
            events.append((kd[k][0], r5))
            last = k
    print(f"A. 코스피 5일 ≤ {CRASH:.0%} 급락 이벤트 {len(events)}회: " +
          ", ".join(f"{d[4:6]}/{d[6:]}({r:+.1%})" for d, r in events[-12:]))
    top, bot, mid, allr = [], [], [], []
    for d, _ in events:
        cands = []
        for c, bs in bars_all.items():
            i = idx[c].get(d)
            if i is None or i < 25 or not liquid(bs, i):
                continue
            cands.append((bs[i]["close"] / bs[i - 5]["close"] - 1, c, i))
        cands.sort()
        n = len(cands)
        if n < 50:
            continue
        q = n // 5
        for grp, sel in ((bot, cands[:q]), (top, cands[-q:]), (mid, cands[2 * q:3 * q])):
            for _, c, i in sel:
                t = trade(bars_all[c], i)
                if t:
                    grp.append(t)
        for _, c, i in cands:
            t = trade(bars_all[c], i)
            if t:
                allr.append(t)
    agg(top, "덜 빠진 상위 20% (초하 3/10)")
    agg(mid, "중간 20%")
    agg(bot, "많이 빠진 하위 20% (역발상)")
    agg(allr, "급락일 전체")

    # ---------- B. 핫테마 안 지각주 vs 대장 ----------
    members = {t: [c for c in cs if c in bars_all] for t, cs in theme_members().items()}
    members = {t: cs for t, cs in members.items() if len(cs) >= 5}
    dates = [d for d, _ in kd]
    lag, lead, midt, hot_events = [], [], [], 0
    for t, cs in members.items():
        last = -99
        for k, d in enumerate(dates):
            if k - last < COOL:
                continue
            rets = []
            for c in cs:
                i = idx[c].get(d)
                if i is None or i < 25:
                    continue
                rets.append((bars_all[c][i]["close"] / bars_all[c][i - 5]["close"] - 1, c, i))
            if len(rets) < 5 or statistics.median(r for r, _, _ in rets) < HOT:
                continue
            hot_events += 1
            last = k
            rets.sort()
            third = max(1, len(rets) // 3)
            for grp, sel in ((lag, rets[:third]), (lead, rets[-third:]), (midt, rets[third:-third] or [])):
                for _, c, i in sel:
                    if liquid(bars_all[c], i):
                        tr = trade(bars_all[c], i)
                        if tr:
                            grp.append(tr)
    print(f"\nB. 핫테마 이벤트(구성 5일 중앙값 ≥ {HOT:.0%}) {hot_events}회 · 테마 {len(members)}개")
    agg(lag, "덜 오른 하위 1/3 (초하 1/21)")
    agg(midt, "중간 1/3")
    agg(lead, "대장 상위 1/3")
    print("\n비교: A급 +0.117R · 전체 무작위 −0.047R · 수수료 미반영 · B의 절대 수치는 지도 룩어헤드로 과대 — 상대 비교만")
    log.ok("bt_relstrength", "done")


if __name__ == "__main__":
    main()
