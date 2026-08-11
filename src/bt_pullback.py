"""R1 — 되돌림 진입 검증. 조하쌤 "오른 폭의 30~50% 눌림에서 사라" 룰.

미션("시작된 것을 일찍 탄다")의 핵심 실험이다. 급등(=시작)을 확인한 뒤,
추격하지 않고 눌림을 기다려 지정가로 받는 방식이 현행 돌파 진입보다 나은가.

정의 (전부 신호일 이전·당일 데이터만):
  급등 확인일 D: 최근 7일 내 저점 L → 고점 H, (H/L−1) ≥ 10%, 고점이 저점보다 뒤.
  진입: 지정가 = H − (H−L)×R,  R ∈ {0.30, 0.40, 0.50, 0.62}
        D+1부터 5일 내 저가가 지정가에 닿으면 체결 (갭 하락 출발이면 시가 체결 — 유리한 쪽이지만
        갭 하락 자체가 나쁜 신호일 수 있어 보수 왜곡은 아님. 동일 규칙을 전 케이스에 적용).
  손절: L × 0.99 (급등 출발 저점 밑 — 구조 손절)
  목표: 진입 + 2R / 최대 20일 보유 / 동시 터치 시 손절 우선.
  유동성: 20일 평균 거래대금 30억+.

대조군: 같은 신호일 D에서 현행 방식 — H 돌파 지정가(H×1.002) + 1.5×ATR 손절 + 2R.

교차: 간밤 미국 해당 테마 +3%↑ 신호가 있는 날만 따로 집계 (실제 운영 조건).
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import backtest
from db import connect
from net import RunLog
from replay import load_bars

MIN_VALUE_EOK = 30.0
HOLD = 20
FILL_WINDOW = 5
SURGE_MIN = 0.10
RETRACE = (0.30, 0.40, 0.50, 0.62)


def sim_from_fill(bs, fidx, fill, stop, target):
    """체결 이후 손절/목표/시간청산. 동시 터치는 손절 우선."""
    n = len(bs)
    for k in range(fidx, min(fidx + HOLD, n)):
        if bs[k]["low"] <= stop:
            return -1.0
        if bs[k]["high"] >= target:
            return (target - fill) / (fill - stop)
    return (bs[min(fidx + HOLD, n) - 1]["close"] - fill) / (fill - stop)


def main():
    log = RunLog()
    bars_all = load_bars()
    log.ok("R1", f"일봉 캐시 {len(bars_all)}종목")

    with connect() as conn:
        stock_themes = {r["code"]: [t.strip() for t in (r["themes"] or "").split(",") if t.strip()]
                        for r in conn.execute("SELECT code, themes FROM stocks")}
    us_hist = backtest.us_theme_history(log)

    # 결과 그릇: {케이스: [r, ...]}
    res: dict[str, list[float]] = {f"되돌림 {int(r*100)}%": [] for r in RETRACE}
    res["대조군: 돌파+1.5ATR"] = []
    res_hot = {k: [] for k in res}          # 미국 테마 신호일만
    fills: dict[str, int] = {k: 0 for k in res}
    signals: dict[str, int] = {k: 0 for k in res}

    for code, bs in bars_all.items():
        n = len(bs)
        if n < 90:
            continue
        c = [b["close"] for b in bs]
        h = [b["high"] for b in bs]
        lo = [b["low"] for b in bs]
        v = [b["close"] * b["volume"] for b in bs]

        themes = stock_themes.get(code, [])

        i = 40
        while i < n - HOLD - FILL_WINDOW - 1:
            val20 = statistics.mean(v[i - 19:i + 1])
            if val20 / 1e8 < MIN_VALUE_EOK:
                i += 1
                continue

            # 급등 확인: 최근 7일 창에서 저점 → 고점 순서로 +10% 이상
            win_lo = min(range(i - 6, i + 1), key=lambda k: lo[k])
            win_hi = max(range(win_lo, i + 1), key=lambda k: h[k])
            L, H = lo[win_lo], h[win_hi]
            if L <= 0 or H / L - 1 < SURGE_MIN or win_hi < win_lo:
                i += 1
                continue

            hot = False
            for t in themes:
                if t in us_hist:
                    u = backtest.prior_us(us_hist[t], bs[i]["date"])
                    if u is not None and u >= 3.0:
                        hot = True
                        break

            # ── 되돌림 케이스들 ──
            for rr in RETRACE:
                key = f"되돌림 {int(rr*100)}%"
                signals[key] += 1
                limit = H - (H - L) * rr
                stop = L * 0.99
                if stop >= limit:
                    continue
                fill = fidx = None
                for k in range(i + 1, min(i + 1 + FILL_WINDOW, n)):
                    if lo[k] <= limit:
                        fill = min(limit, bs[k]["open"])
                        fidx = k
                        break
                if fill is None or fill <= stop:
                    continue
                fills[key] += 1
                r = sim_from_fill(bs, fidx, fill, stop, fill + 2 * (fill - stop))
                res[key].append(r)
                if hot:
                    res_hot[key].append(r)

            # ── 대조군: 현행 돌파 ──
            key = "대조군: 돌파+1.5ATR"
            signals[key] += 1
            atr = statistics.mean(
                max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1]))
                for k in range(i - 13, i + 1))
            entry = H * 1.002
            stop = c[i] - 1.5 * atr
            if stop > 0 and stop < entry:
                fill = fidx = None
                for k in range(i + 1, min(i + 4, n)):      # 돌파는 3일 창 (기존 규약)
                    if h[k] >= entry:
                        fill = max(entry, bs[k]["open"])
                        fidx = k
                        break
                if fill is not None and fill > stop:
                    fills[key] += 1
                    r = sim_from_fill(bs, fidx, fill, stop, fill + 2 * (fill - stop))
                    res[key].append(r)
                    if hot:
                        res_hot[key].append(r)

            i += 5   # 같은 급등 중복 신호 방지

    def table(data, title):
        print(f"\n  ▸ {title}")
        print(f"    {'케이스':<20}{'표본':>7}{'승률':>8}{'평균':>9}{'중앙값':>9}")
        print("    " + "-" * 53)
        for key, rs in data.items():
            if len(rs) < 100:
                print(f"    {key:<20}{len(rs):>7,}  표본 부족")
                continue
            win = sum(1 for r in rs if r > 0) / len(rs) * 100
            print(f"    {key:<20}{len(rs):>7,}{win:>7.1f}%{statistics.mean(rs):>+8.3f}R"
                  f"{statistics.median(rs):>+8.2f}R")

    print("\n" + "=" * 70)
    print("R1 — 급등 확인 후 되돌림 진입 vs 현행 돌파 진입 (목표 2R 공통)")
    print("=" * 70)
    for key in res:
        fr = fills[key] / signals[key] * 100 if signals[key] else 0
        print(f"  {key:<20} 신호 {signals[key]:>7,} → 체결 {fills[key]:>6,} ({fr:4.1f}%)")
    table(res, "전체")
    table(res_hot, "간밤 미국 해당 테마 +3%↑ 신호일만 (실운영 조건)")
    print("\n  ※ 수수료 미반영. 되돌림 손절은 급등 출발 저점 밑(구조), 대조군은 1.5ATR.")


if __name__ == "__main__":
    main()
