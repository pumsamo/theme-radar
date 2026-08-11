"""R2 — '초기' 정량화. 바닥 대비 얼마나 오른 시점까지가 "일찍"인가.

R1에서 되돌림 진입은 기각됐다(눌림 자체가 나쁜 신호). 그럼 진입 방식은 현행 돌파를
유지하되, **신호일에 그 종목이 바닥에서 얼마나 올라와 있었는지**로 초기/후기를 가른다.

측정: 신호일 종가가 20일 저점 대비 +X% 구간별로, 동일한 진입(전일 고가 돌파)·
손절(1.5ATR)·목표(2R)의 성적. 미국 테마 +3%↑ 신호일만 따로 교차.
유동성 30억+, 동시 터치 손절 우선 — 전부 기존 규약.
"""
from __future__ import annotations

import statistics

import boot  # noqa: F401
import backtest
from bt_pullback import sim_from_fill
from db import connect
from net import RunLog
from replay import load_bars

MIN_VALUE_EOK = 30.0
BUCKETS = [(0, 5, "① 0~5% (바닥권)"), (5, 15, "② 5~15%"), (15, 30, "③ 15~30%"),
           (30, 50, "④ 30~50%"), (50, 999, "⑤ 50%+ (한참 감)")]


def bucket_of(x):
    for lo_, hi_, name in BUCKETS:
        if lo_ <= x < hi_:
            return name
    return None


def main():
    log = RunLog()
    bars_all = load_bars()
    log.ok("R2", f"일봉 캐시 {len(bars_all)}종목")

    with connect() as conn:
        stock_themes = {r["code"]: [t.strip() for t in (r["themes"] or "").split(",") if t.strip()]
                        for r in conn.execute("SELECT code, themes FROM stocks")}
    us_hist = backtest.us_theme_history(log)

    res: dict[str, list[float]] = {name: [] for _, _, name in BUCKETS}
    res_hot: dict[str, list[float]] = {name: [] for _, _, name in BUCKETS}

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
        while i < n - 24:
            val20 = statistics.mean(v[i - 19:i + 1])
            if val20 / 1e8 < MIN_VALUE_EOK:
                i += 1
                continue
            low20 = min(lo[i - 19:i + 1])
            if low20 <= 0:
                i += 1
                continue
            rise = (c[i] / low20 - 1) * 100
            name = bucket_of(rise)
            if not name:
                i += 1
                continue

            atr = statistics.mean(
                max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1]))
                for k in range(i - 13, i + 1))
            entry = h[i] * 1.002
            stop = c[i] - 1.5 * atr
            if stop <= 0 or stop >= entry:
                i += 1
                continue
            fill = fidx = None
            for k in range(i + 1, min(i + 4, n)):
                if h[k] >= entry:
                    fill = max(entry, bs[k]["open"])
                    fidx = k
                    break
            if fill is None or fill <= stop:
                i += 1
                continue
            r = sim_from_fill(bs, fidx, fill, stop, fill + 2 * (fill - stop))
            res[name].append(r)

            for t in themes:
                if t in us_hist:
                    u = backtest.prior_us(us_hist[t], bs[i]["date"])
                    if u is not None and u >= 3.0:
                        res_hot[name].append(r)
                        break
            i += 3

    def table(data, title):
        print(f"\n  ▸ {title}")
        print(f"    {'20일 저점 대비':<20}{'표본':>8}{'승률':>8}{'평균':>9}{'중앙값':>9}")
        print("    " + "-" * 55)
        for _, _, name in BUCKETS:
            rs = data[name]
            if len(rs) < 100:
                print(f"    {name:<20}{len(rs):>8,}  표본 부족")
                continue
            win = sum(1 for r in rs if r > 0) / len(rs) * 100
            print(f"    {name:<20}{len(rs):>8,}{win:>7.1f}%{statistics.mean(rs):>+8.3f}R"
                  f"{statistics.median(rs):>+8.2f}R")

    print("\n" + "=" * 70)
    print("R2 — 신호일의 '바닥 대비 위치'별 성적 (진입·손절·목표는 현행과 동일)")
    print("=" * 70)
    table(res, "전체")
    table(res_hot, "간밤 미국 해당 테마 +3%↑ 신호일만 (실운영 조건)")
    print("\n  ※ 수수료 미반영. '초기'가 유효하다면 ②~③ 구간이 ①과 ⑤보다 좋아야 한다.")


if __name__ == "__main__":
    main()
