"""지시문의 두 가지 전제를 테마 단위로 그대로 검증한다.

  Q1. 간밤 미국에서 오른 테마가, 다음 한국 거래일에 같은 테마로 올랐나? (4-3)
  Q2. 국내 뉴스에 뜬 테마가, 실제로 한국장에서 올랐나? (4-4)

매매 성패가 아니라 **테마가 올랐냐**만 본다. 차트 자리·손절·손익비는 여기 안 낀다.

한국 테마의 하루 등락 = 그 테마 소속 종목들의 당일 등락률 중앙값.
(평균이 아니라 중앙값 — 상한가 한 종목이 테마 전체를 올린 것처럼 보이게 하면 안 되니까)
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import boot  # noqa: F401
import backtest
import themes_cfg
from db import connect
from net import CACHE_DIR, RunLog

OHLC_CACHE = CACHE_DIR / "ohlc"
MIN_MEMBERS = 4     # 테마 하루 등락을 계산하려면 최소 이만큼은 시세가 있어야 한다


def kr_theme_series(log: RunLog) -> dict[str, dict[str, float]]:
    """{테마: {날짜(YYYYMMDD): 소속 종목 등락률 중앙값}}"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT code, themes FROM stocks WHERE themes IS NOT NULL").fetchall()

    theme_codes: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        for t in (x.strip() for x in r["themes"].split(",")):
            if t:
                theme_codes[t].append(r["code"])

    # 백테스트가 받아둔 일봉 캐시를 그대로 쓴다 (재수집 안 함)
    cache: dict[str, dict[str, float]] = {}
    for path in OHLC_CACHE.glob("*.json"):
        code = path.name.split("_")[0]
        try:
            bars = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        chg = {}
        for prev, cur in zip(bars, bars[1:]):
            if prev["close"] > 0:
                chg[cur["date"]] = (cur["close"] / prev["close"] - 1) * 100
        if chg:
            cache[code] = chg
    log.ok("verify", f"일봉 캐시 {len(cache)}종목")

    out: dict[str, dict[str, float]] = {}
    for theme, codes in theme_codes.items():
        got = [cache[c] for c in codes if c in cache]
        if len(got) < MIN_MEMBERS:
            continue
        dates = set().union(*(set(g) for g in got))
        series = {}
        for d in dates:
            vals = [g[d] for g in got if d in g]
            if len(vals) >= MIN_MEMBERS:
                series[d] = statistics.median(vals)
        if series:
            out[theme] = series
    log.ok("verify", f"한국 테마 시계열 {len(out)}개 (소속 {MIN_MEMBERS}종목 이상)")
    return out


def prior_us_date(us_series: dict[str, float], kr_date: str) -> float | None:
    for back in range(1, 6):
        d = (datetime.strptime(kr_date, "%Y%m%d").date() - timedelta(days=back)).isoformat()
        if d in us_series:
            return us_series[d]
    return None


def q1(kr: dict, us: dict, log: RunLog) -> None:
    print("\n" + "=" * 70)
    print("Q1. 간밤 미국에서 오른 테마가, 다음 한국 거래일에 올랐나?")
    print("=" * 70)

    pairs: list[tuple[float, float]] = []
    for theme, us_series in us.items():
        if theme not in kr:
            continue
        for d, kr_chg in kr[theme].items():
            u = prior_us_date(us_series, d)
            if u is not None:
                pairs.append((u, kr_chg))

    if not pairs:
        print("  비교 가능한 날이 없다")
        return

    base_up = sum(1 for _, k in pairs if k > 0) / len(pairs) * 100
    base_mean = statistics.mean(k for _, k in pairs)
    print(f"\n  전체 {len(pairs):,}건 (테마 × 거래일)")
    print(f"  기준선: 한국 테마가 오른 날 {base_up:.1f}% · 평균 {base_mean:+.2f}%\n")

    buckets = [(-99, -3, "미국 -3% 미만"), (-3, -1, "미국 -3~-1%"),
               (-1, 1, "미국 -1~+1%"), (1, 3, "미국 +1~+3%"),
               (3, 5, "미국 +3~+5%"), (5, 99, "미국 +5% 이상")]
    print(f"  {'간밤 미국':<16}{'표본':>7}{'한국 상승확률':>14}{'한국 평균':>12}{'기준선대비':>12}")
    print("  " + "-" * 61)
    for lo, hi, label in buckets:
        sel = [k for u, k in pairs if lo <= u < hi]
        if len(sel) < 30:
            continue
        up = sum(1 for k in sel if k > 0) / len(sel) * 100
        mean = statistics.mean(sel)
        print(f"  {label:<16}{len(sel):>7,}{up:>13.1f}%{mean:>11.2f}%{mean - base_mean:>+11.2f}%p")

    # 상관계수
    us_v = [u for u, _ in pairs]
    kr_v = [k for _, k in pairs]
    try:
        r = statistics.correlation(us_v, kr_v)
        print(f"\n  상관계수 r = {r:+.3f}  (0에 가까우면 관계 없음, 1에 가까우면 같이 움직임)")
    except Exception:  # noqa: BLE001
        pass

    strong = [k for u, k in pairs if u >= 3]
    if len(strong) >= 30:
        up = sum(1 for k in strong if k > 0) / len(strong) * 100
        print(f"\n  ▶ 미국이 +3% 이상 오른 다음 날: 한국 같은 테마가 오를 확률 {up:.1f}% "
              f"(평소 {base_up:.1f}%)")
        print(f"    평균 등락 {statistics.mean(strong):+.2f}% (평소 {base_mean:+.2f}%)")


def q2(kr: dict, log: RunLog) -> None:
    print("\n" + "=" * 70)
    print("Q2. 국내 뉴스에 뜬 테마가, 실제로 한국장에서 올랐나?")
    print("=" * 70)

    with connect() as conn:
        sigs = conn.execute(
            """SELECT date, kr_theme, direction, COUNT(*) n FROM news_signals
               GROUP BY date, kr_theme, direction""").fetchall()
        span = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM news_signals").fetchone()

    print(f"\n  보유 뉴스 신호: {span[0]} ~ {span[1]} ({span[2]}일치, {len(sigs)}건)")
    print("\n  ⚠ 이 질문은 지금 답할 수 없다.")
    print("    RSS는 최근 기사만 준다. 과거 뉴스를 못 가져오니 '뉴스가 먼저 떴는지'를")
    print("    과거 시점으로 되돌려 확인할 방법이 없다. 시드 엑셀의 '상승 이유'는")
    print("    이미 오른 뒤에 붙인 설명이라 선행 신호로 쓸 수 없다.")
    print("\n    → 오늘부터 매일 뉴스 신호가 DB에 쌓인다. 2~3개월 뒤 같은 검증을 돌리면")
    print("      그때는 답할 수 있다. 그 전까지 뉴스 층의 성적은 '미측정'이다.")

    matched = 0
    for s in sigs:
        d = s["date"].replace("-", "")
        if s["kr_theme"] in kr and d in kr[s["kr_theme"]]:
            matched += 1
            print(f"      · {s['date']} {s['kr_theme']} [{s['direction']}] "
                  f"→ 당일 테마 {kr[s['kr_theme']][d]:+.2f}%")
    if not matched:
        print("\n    (현재 보유 신호는 전부 장이 열리기 전이라 대조할 등락이 아직 없다)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.parse_args()

    log = RunLog()
    kr = kr_theme_series(log)
    us = backtest.us_theme_history(log)
    q1(kr, us, log)
    q2(kr, log)
    print("\n※ 테마별 소속 종목은 2026-07~08 급등 집계로 만든 지도다. 과거 구간에선")
    print("  당시 대장주와 다를 수 있어 절대 수치보다 구간 간 '차이'를 보는 게 맞다.")
