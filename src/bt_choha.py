"""초하쌤 '삼박자' 매수 자리 검증 (무지랭이1 6강 정의를 일봉 규칙으로 옮김).

신호일 i (전부 i 이전 데이터만):
  ① RSI 탈출 : 최근 5일 중 RSI14 최저 < TH(30 또는 40) 이고 오늘 RSI ≥ TH
  ② 볼밴 복귀 : 최근 5일 중 종가가 볼린저(20,2) 하단 밑이었고, 오늘 종가는 하단 위 + 양봉
  ③ 장기이평 근접: 종가가 MA112 또는 MA224의 -8%~+8% 안 (448일선은 캐시 길이상 대부분 미계산)
  유동성: 20일 평균 거래대금 30억+

진입 두 방식:  (A) 다음날 시가  (B) 신호일 고가×1.002 돌파 (3일 내, 우리 방식)
손절: 최근 7일 최저가 × 0.99 (초하쌤 "진입 근처 1주일 최저점")
목표 +2R, 20일 보유, 동시 터치 시 손절 우선, 수수료 미반영.

대조군: 같은 청산 규칙으로 (가) 전체 날짜 (나) 우리 A급 자리.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
from net import RunLog
from replay import load_bars

MIN_VALUE_EOK = 30.0
HOLD, R_TARGET, ENTRY_WIN = 20, 2.0, 3


def rsi_series(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    gains = [max(c[k] - c[k - 1], 0) for k in range(1, len(c))]
    losses = [max(c[k - 1] - c[k], 0) for k in range(1, len(c))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100 - 100 / (1 + ag / al) if al else 100.0
    for k in range(n + 1, len(c)):
        ag = (ag * (n - 1) + gains[k - 1]) / n
        al = (al * (n - 1) + losses[k - 1]) / n
        out[k] = 100 - 100 / (1 + ag / al) if al else 100.0
    return out


def sma(c, i, w):
    return sum(c[i - w + 1:i + 1]) / w if i >= w - 1 else None


def sim(bs, i, entry_mode, stop):
    c = [b["close"] for b in bs]
    h = [b["high"] for b in bs]
    lo = [b["low"] for b in bs]
    n = len(bs)
    if entry_mode == "A":
        if i + 1 >= n or bs[i + 1]["open"] <= stop:
            return None
        fill, fidx = bs[i + 1]["open"], i + 1
    else:
        entry = h[i] * 1.002
        fill = fidx = None
        for k in range(1, ENTRY_WIN + 1):
            if i + k >= n:
                break
            if h[i + k] >= entry:
                fill, fidx = max(entry, bs[i + k]["open"]), i + k
                break
        if fill is None or fill <= stop:
            return None
    if fidx + HOLD > n:
        return None
    risk = fill - stop
    target = fill + R_TARGET * risk
    for k in range(fidx, fidx + HOLD):
        if lo[k] <= stop:
            return -1.0
        if h[k] >= target:
            return R_TARGET
    return (c[fidx + HOLD - 1] - fill) / risk


def main():
    log = RunLog()
    bars_all = load_bars()
    log.ok("choha", f"종목 {len(bars_all)}개")

    groups = {k: {"A": [], "B": []} for k in (
        "삼박자(RSI30)", "삼박자(RSI40)", "①②만(이평 무관)", "③이평근접만",
        "대조: 우리 A급 자리", "대조: 전체")}

    for code, bs in bars_all.items():
        n = len(bs)
        if n < 260:
            continue
        c = [b["close"] for b in bs]
        o = [b["open"] for b in bs]
        h = [b["high"] for b in bs]
        lo = [b["low"] for b in bs]
        v = [b["close"] * b["volume"] for b in bs]
        rsi = rsi_series(c)
        last_sig = -99
        for i in range(230, n - HOLD - ENTRY_WIN - 1):
            if i % 3 != 0 and False:
                pass
            val20 = statistics.mean(v[i - 19:i + 1])
            if val20 / 1e8 < MIN_VALUE_EOK or c[i] <= 0:
                continue
            stop = min(lo[i - 6:i + 1]) * 0.99
            if stop >= c[i]:
                continue
            ma20 = sma(c, i, 20)
            sd = statistics.pstdev(c[i - 19:i + 1])
            lower = ma20 - 2 * sd
            ma112, ma224 = sma(c, i, 112), sma(c, i, 224)
            r_now = rsi[i]
            r_min5 = min(x for x in rsi[i - 4:i + 1] if x is not None)
            below5 = any(c[k] < (sma(c, k, 20) - 2 * statistics.pstdev(c[k - 19:k + 1]))
                         for k in range(i - 4, i))
            bb_back = below5 and c[i] > lower and c[i] > o[i]
            near_lt = any(ma and 0.92 <= c[i] / ma <= 1.08 for ma in (ma112, ma224))
            rsi30 = r_min5 < 30 <= r_now
            rsi40 = r_min5 < 40 <= r_now

            # 대조: 전체 (5일마다 샘플)
            if i % 5 == 0:
                for m in ("A", "B"):
                    r = sim(bs, i, m, stop)
                    if r is not None:
                        groups["대조: 전체"][m].append(r)
            # 대조: 우리 A급 자리
            hi60 = max(h[i - 59:i + 1])
            from_hi = (c[i] / hi60 - 1) * 100
            ext = c[i] / ma20
            if -15 <= from_hi <= -3 and 0.95 <= ext <= 1.20 and 45 <= r_now <= 75 and i % 3 == 0:
                for m in ("A", "B"):
                    r = sim(bs, i, m, stop)
                    if r is not None:
                        groups["대조: 우리 A급 자리"][m].append(r)

            if i - last_sig < 5:
                continue
            hit = []
            if bb_back and rsi30 and near_lt:
                hit.append("삼박자(RSI30)")
            if bb_back and rsi40 and near_lt:
                hit.append("삼박자(RSI40)")
            if bb_back and rsi40:
                hit.append("①②만(이평 무관)")
            if near_lt and rsi40 and not bb_back:
                hit.append("③이평근접만")
            if not hit:
                continue
            last_sig = i
            for m in ("A", "B"):
                r = sim(bs, i, m, stop)
                if r is None:
                    continue
                for k in hit:
                    groups[k][m].append(r)

    print("\n" + "=" * 78)
    print("초하쌤 삼박자 자리 — 2년 검증 (손절 7일 최저×0.99 · 목표 2R · 20일 · 수수료 미반영)")
    print("=" * 78)
    print(f"  {'구간':<20}{'진입':>4}{'표본':>8}{'승률':>8}{'평균R':>9}{'중앙값':>8}")
    print("  " + "-" * 60)
    for k, d in groups.items():
        for m in ("A", "B"):
            vals = d[m]
            if len(vals) < 30:
                print(f"  {k:<20}{m:>4}{len(vals):>8}   표본 부족")
                continue
            win = sum(1 for r in vals if r > 0) / len(vals) * 100
            print(f"  {k:<20}{m:>4}{len(vals):>8,}{win:>7.1f}%{statistics.mean(vals):>+9.3f}"
                  f"{statistics.median(vals):>+8.2f}")
    print("\n  A = 다음날 시가 매수(초하쌤식)  B = 신호일 고가 돌파 시에만(우리식)")


if __name__ == "__main__":
    main()
