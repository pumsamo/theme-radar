"""R3 — 손절 방식 대결. 같은 신호·같은 진입에서 손절만 바꿔 비교한다.

R1: 진입은 현행 돌파가 승자. R2: '초기' 필터는 판별력 없음 → 진입·자리 규칙은 유지.
남은 후보는 손절 — bt_bottom 대조군에서 '구조 밑 타이트'가 +0.21R로 ATR 방식을
크게 이겼는데, 그건 횡보 돌파 한정이었다. 우리 실제 신호(A급 자리)에서도 이기는지 확인.

신호: 현행 v1 픽 조건 그대로 — A급 자리(3/3) + 유동성 + 거래량. (재현: backtest.py 규약)
진입: 신호일 고가 돌파 (3일 창, 갭이면 시가).
손절 후보:
  (a) 현행: 종가 − 1.5×ATR14
  (b) 구조: 최근 10일 저가 × 0.99  (타이트 — 조하쌤 방식)
  (c) 구조: 최근 15일 저가 × 0.99  (여유)
목표: 진입 + 2×(진입−손절) — 손절폭에 비례하므로 (b)는 목표도 가깝다.
교차: 전체 vs 간밤 미국 테마 +3%↑.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import backtest
import prices_kr
import screen
from db import connect
from net import RunLog
from replay import load_bars
from bt_pullback import sim_from_fill

VARIANTS = ("(a) 1.5ATR (현행)", "(b) 10일 저가 밑", "(c) 15일 저가 밑")


def main():
    log = RunLog()
    bars_all = load_bars()
    log.ok("R3", f"일봉 캐시 {len(bars_all)}종목")

    with connect() as conn:
        stock_themes = {r["code"]: [t.strip() for t in (r["themes"] or "").split(",") if t.strip()]
                        for r in conn.execute("SELECT code, themes FROM stocks")}
    us_hist = backtest.us_theme_history(log)

    res = {v: [] for v in VARIANTS}
    res_hot = {v: [] for v in VARIANTS}
    risk_pct = {v: [] for v in VARIANTS}       # 손절폭(진입가 대비 %) — 체감 비교용

    n_sig = 0
    for code, bs in bars_all.items():
        n = len(bs)
        if n < 90:
            continue
        lo = [b["low"] for b in bs]
        h = [b["high"] for b in bs]
        themes = stock_themes.get(code, [])

        for i in range(60, n - 24, 1):
            ind = prices_kr.indicators(bs[:i + 1])
            if not ind:
                continue
            if ind["value20_eok"] < screen.MIN_VALUE_EOK:
                continue
            if not ind["vol_ratio"] or ind["vol_ratio"] < 0.8:
                continue
            plan = screen.make_plan(ind)
            if not plan["entry"] or plan.get("grade") != "A":
                continue
            n_sig += 1

            hot = False
            for t in themes:
                if t in us_hist:
                    u = backtest.prior_us(us_hist[t], bs[i]["date"])
                    if u is not None and u >= 3.0:
                        hot = True
                        break

            entry = ind["high"] * 1.002
            stops = {
                "(a) 1.5ATR (현행)": ind["close"] - 1.5 * (ind["atr"] or 0),
                "(b) 10일 저가 밑": min(lo[i - 9:i + 1]) * 0.99,
                "(c) 15일 저가 밑": min(lo[i - 14:i + 1]) * 0.99,
            }
            fill = fidx = None
            for k in range(i + 1, min(i + 4, n)):
                if h[k] >= entry:
                    fill = max(entry, bs[k]["open"])
                    fidx = k
                    break
            if fill is None:
                continue
            for name, stop in stops.items():
                if stop <= 0 or stop >= fill:
                    continue
                r = sim_from_fill(bs, fidx, fill, stop, fill + 2 * (fill - stop))
                res[name].append(r)
                risk_pct[name].append((fill - stop) / fill * 100)
                if hot:
                    res_hot[name].append(r)

    def table(data, title):
        print(f"\n  ▸ {title}")
        print(f"    {'손절 방식':<20}{'표본':>7}{'승률':>8}{'평균':>9}{'중앙값':>9}{'손절폭':>8}")
        print("    " + "-" * 62)
        for name in VARIANTS:
            rs = data[name]
            if len(rs) < 100:
                print(f"    {name:<20}{len(rs):>7,}  표본 부족")
                continue
            win = sum(1 for r in rs if r > 0) / len(rs) * 100
            rp = statistics.mean(risk_pct[name])
            print(f"    {name:<20}{len(rs):>7,}{win:>7.1f}%{statistics.mean(rs):>+8.3f}R"
                  f"{statistics.median(rs):>+8.2f}R{rp:>7.1f}%")

    print("\n" + "=" * 72)
    print(f"R3 — 손절 방식 대결 (신호 {n_sig:,}건 · A급 자리 + 유동성 + 거래, 진입·목표 동일)")
    print("=" * 72)
    table(res, "전체")
    table(res_hot, "간밤 미국 해당 테마 +3%↑ (실운영 조건)")
    print("\n  ※ 손절폭 = 진입가 대비 %. R이 같아도 손절폭이 다르면 원금 손익은 다르다.")
    print("  ※ 수수료 미반영.")


if __name__ == "__main__":
    main()
