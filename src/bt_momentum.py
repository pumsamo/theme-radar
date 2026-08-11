"""스윙 호흡의 모멘텀 로테이션 백테스트.

규칙 (전부 종가 기준 — 장중 볼 필요 없음, 직장인 전제):
  N거래일마다 리밸런싱. 유동성(20일 평균 거래대금) 통과 종목을
  '최근 L거래일 수익률'로 줄 세워 상위 K개를 동일가중 매수, 다음 리밸런싱까지 보유.
  비용: 리밸런싱마다 왕복 0.25% 차감 (거래세+수수료+슬리피지 근사).

비교군:
  ① 5일 로테이션 (주 1회 주문 — 스윙)
  ② 10일 로테이션 (2주 1회)
  ③ 20일 로테이션 (월 1회)
  기준선: 유동성 통과 전 종목 동일가중 보유 (그냥 시장에 있었을 때)

모멘텀 길이 L: 20일 / 60일 두 가지. 상위 K=10.
"""
from __future__ import annotations

import argparse
import statistics

import boot  # noqa: F401
from net import RunLog
from replay import load_bars

MIN_VALUE_EOK = 50.0
TOP_K = 10
COST = 0.0025          # 왕복 0.25%


def build(bars_all, log):
    cal = [b["date"] for b in bars_all["005930"]]
    di = {d: i for i, d in enumerate(cal)}

    # 종목별 종가·거래대금을 달력 인덱스에 정렬
    px: dict[str, list] = {}
    val: dict[str, list] = {}
    for code, bs in bars_all.items():
        p = [None] * len(cal)
        v = [None] * len(cal)
        for b in bs:
            i = di.get(b["date"])
            if i is not None:
                p[i] = b["close"]
                v[i] = b["close"] * b["volume"]
        px[code] = p
        val[code] = v
    log.ok("mom", f"달력 {len(cal)}일 · 종목 {len(px)}개")
    return cal, px, val


def val20_ok(v, i):
    seg = [x for x in v[max(0, i - 19):i + 1] if x is not None]
    return len(seg) >= 15 and statistics.mean(seg) / 1e8 >= MIN_VALUE_EOK


def run_strategy(cal, px, val, lookback, hold, log):
    """리밸런싱 시점마다 상위 K 매수 → hold일 뒤 성과. 누적 수익률 반환."""
    equity = 1.0
    rets = []
    start = max(lookback + 21, 61)
    i = start
    while i + hold < len(cal):
        # 후보: 유동성 + 모멘텀 계산 가능
        scored = []
        for code in px:
            p = px[code]
            if p[i] is None or p[i - lookback] is None or not p[i - lookback]:
                continue
            if not val20_ok(val[code], i):
                continue
            scored.append((p[i] / p[i - lookback] - 1, code))
        if len(scored) < TOP_K * 2:
            i += hold
            continue
        scored.sort(reverse=True)
        basket = [c for _, c in scored[:TOP_K]]

        # hold일 보유 수익 (동일가중, 중간 결측은 마지막 가격으로)
        prs = []
        for c in basket:
            p0, p1 = px[c][i], px[c][i + hold]
            if p0 and p1:
                prs.append(p1 / p0 - 1)
        if prs:
            r = statistics.mean(prs) - COST
            equity *= (1 + r)
            rets.append(r)
        i += hold

    if not rets:
        return None
    n_year = 252 / hold
    win = sum(1 for r in rets if r > 0) / len(rets) * 100
    mdd = 0.0
    eq, peak = 1.0, 1.0
    for r in rets:
        eq *= (1 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return {
        "n": len(rets), "total": (equity - 1) * 100,
        "cagr": ((equity ** (n_year / len(rets))) - 1) * 100,
        "win": win, "avg": statistics.mean(rets) * 100,
        "mdd": mdd * 100,
    }


def run_baseline(cal, px, val, log):
    """유동성 통과 전 종목 동일가중 — '그냥 시장에 있었다면'."""
    start = 81
    equity = 1.0
    rets = []
    for i in range(start, len(cal) - 20, 20):
        prs = []
        for code in px:
            p = px[code]
            if p[i] and p[i + 20] and val20_ok(val[code], i):
                prs.append(p[i + 20] / p[i] - 1)
        if prs:
            r = statistics.mean(prs)
            equity *= (1 + r)
            rets.append(r)
    mdd, eq, peak = 0.0, 1.0, 1.0
    for r in rets:
        eq *= (1 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return {"total": (equity - 1) * 100, "mdd": mdd * 100, "n": len(rets)}


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()
    log = RunLog()
    bars_all = load_bars()
    cal, px, val = build(bars_all, log)

    base = run_baseline(cal, px, val, log)
    print("\n" + "=" * 74)
    print(f"스윙 모멘텀 로테이션 — 상위 {TOP_K}종목 동일가중 · 왕복비용 {COST*100:.2f}% 반영")
    print(f"기간: {cal[0][:4]}-{cal[0][4:6]} ~ {cal[-1][:4]}-{cal[-1][4:6]} ({len(cal)}거래일)")
    print("=" * 74)
    print(f"\n  기준선(시장 그냥 보유): 누적 {base['total']:+.1f}% · MDD {base['mdd']:.1f}%\n")

    print(f"  {'전략':<24}{'회전':>5}{'승률':>8}{'회당평균':>9}{'누적':>10}{'연환산':>9}{'MDD':>8}")
    print("  " + "-" * 71)
    for lookback, lb_name in ((20, "1개월 모멘텀"), (60, "3개월 모멘텀")):
        for hold, h_name in ((5, "5일"), (10, "10일"), (20, "20일")):
            s = run_strategy(cal, px, val, lookback, hold, log)
            if not s:
                continue
            print(f"  {lb_name + ' × ' + h_name + '보유':<24}{s['n']:>5}"
                  f"{s['win']:>7.1f}%{s['avg']:>8.2f}%{s['total']:>+9.1f}%"
                  f"{s['cagr']:>+8.1f}%{s['mdd']:>7.1f}%")

    print("\n  ※ 종가 리밸런싱 가정 — 실제로는 다음날 시가 체결이라 약간 불리해질 수 있다.")
    print("  ※ 생존 편향 주의: 현재 상장 종목 기준이라 상장폐지된 종목이 빠져 있다.")


if __name__ == "__main__":
    main()
