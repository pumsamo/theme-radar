"""장 마감 후 채점 — 그날 아침에 내보낸 것이 실제로 맞았나.

세 가지를 각각 따로 채점한다. 뭉뚱그리면 어느 층이 맞고 틀렸는지 안 보인다.
  ① read-across: 간밤 미국에서 오른 테마가 오늘 한국에서 올랐나
  ② 국내발(뉴스): 뉴스로 뽑은 테마가 올랐나
  ③ 오늘의 픽: 진입 트리거가 실제로 터졌나, 손절·목표는 어떻게 됐나

하루치는 표본 1이다. 이걸로 규칙을 바꾸지 말 것 — 쌓아서 보는 용도다(지시문 Phase 3).
"""
from __future__ import annotations

import argparse
import statistics
from datetime import date as _date
from datetime import datetime, timedelta

import boot  # noqa: F401
import prices_kr
from db import connect
from net import RunLog


def kr_moves(date_c: str, log: RunLog) -> dict[str, dict]:
    """{code: {chg_pct, high, low, close}} — 채점일 하루치.

    지표(MA·ATR)는 필요 없고 그날 봉만 있으면 된다. 네이버 endTime은 그날을 포함하지
    않는 경우가 있어 하루 뒤까지 요청하고, 마지막 두 봉으로 등락률을 직접 계산한다.
    """
    from concurrent.futures import ThreadPoolExecutor

    with connect() as conn:
        codes = [r["code"] for r in conn.execute(
            "SELECT DISTINCT code FROM stocks WHERE themes IS NOT NULL")]

    d = datetime.strptime(date_c, "%Y-%m-%d")
    start = (d - timedelta(days=14)).strftime("%Y%m%d")
    end = (d + timedelta(days=1)).strftime("%Y%m%d")
    day = d.strftime("%Y%m%d")

    def one(code: str):
        try:
            rows = prices_kr.fetch_ohlc(code, start, end)
        except Exception:  # noqa: BLE001
            return code, None
        if len(rows) < 2 or rows[-1]["date"] != day:
            return code, None
        cur, prev = rows[-1], rows[-2]
        if prev["close"] <= 0:
            return code, None
        return code, {
            "date": cur["date"], "close": cur["close"],
            "high": cur["high"], "low": cur["low"],
            "chg_pct": round((cur["close"] / prev["close"] - 1) * 100, 2),
        }

    out: dict[str, dict] = {}
    fails = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for code, m in pool.map(one, codes):
            if m:
                out[code] = m
            else:
                fails += 1
    if fails:
        log.warn("score", f"{day} 시세 없음 {fails}종목 (거래정지·신규상장 등)")
    if not out:
        log.warn("score", f"{day} 시세가 하나도 없다 — 휴장일이거나 아직 반영 전")
    else:
        log.ok("score", f"{day} 시세 {len(out)}종목")
    return out


def theme_members() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    with connect() as conn:
        for r in conn.execute("SELECT code, themes FROM stocks WHERE themes IS NOT NULL"):
            for t in (x.strip() for x in r["themes"].split(",")):
                if t:
                    out.setdefault(t, []).append(r["code"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="장 마감 후 채점")
    ap.add_argument("--date", default=_date.today().isoformat())
    args = ap.parse_args()
    date_c = args.date
    log = RunLog()

    moves = kr_moves(date_c, log)
    if not moves:
        raise SystemExit("채점할 시세가 없다")
    members = theme_members()

    with connect() as conn:
        us = {r["kr_theme"]: r["avg_change"] for r in conn.execute(
            "SELECT kr_theme, avg_change FROM theme_daily WHERE date=? AND source='us_basket'",
            (date_c,))}
        asof = conn.execute(
            "SELECT asof FROM global_baseline WHERE date=? AND symbol='^SOX'",
            (date_c,)).fetchone()
        news = {r["kr_theme"]: r for r in conn.execute("""
            SELECT kr_theme,
                   SUM(CASE direction WHEN '호재' THEN weight
                       WHEN '죽은테마' THEN -weight WHEN '악재' THEN -weight*0.6 ELSE 0 END) score
            FROM news_signals WHERE date >= date(?, '-3 day') GROUP BY kr_theme""", (date_c,))}
        picks = [dict(r) for r in conn.execute(
            "SELECT * FROM candidates WHERE date=? AND tier='pick' ORDER BY score DESC",
            (date_c,))]

    # 그날 전체 종목 등락 = 기준선
    allchg = [m["chg_pct"] for m in moves.values()]
    base_up = sum(1 for c in allchg if c > 0) / len(allchg) * 100
    base_mean = statistics.median(allchg)

    def theme_move(theme: str) -> tuple[float, int] | None:
        vals = [moves[c]["chg_pct"] for c in members.get(theme, []) if c in moves]
        return (round(statistics.median(vals), 2), len(vals)) if len(vals) >= 3 else None

    print("\n" + "=" * 74)
    print(f"장 마감 채점  {date_c}")
    print("=" * 74)
    # '장이 좋았다/나빴다'는 지수(대형주)와 테마주가 갈리는 날이 많다 — 둘 다 보여준다.
    day8 = datetime.strptime(date_c, "%Y-%m-%d").strftime("%Y%m%d")
    for sym, nm in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        try:
            idx = prices_kr.fetch_ohlc(sym, (datetime.strptime(date_c, "%Y-%m-%d")
                                             - timedelta(days=10)).strftime("%Y%m%d"),
                                       (datetime.strptime(date_c, "%Y-%m-%d")
                                        + timedelta(days=1)).strftime("%Y%m%d"))
            if idx and idx[-1]["date"] == day8 and len(idx) >= 2:
                chg = (idx[-1]["close"] / idx[-2]["close"] - 1) * 100
                print(f"  {nm} 지수: {idx[-1]['close']:,.2f} ({chg:+.2f}%)", end="")
        except Exception:  # noqa: BLE001
            pass
    print()
    print(f"  기준선(테마 소속 {len(moves)}종목 중앙값): 상승 {base_up:.1f}% · {base_mean:+.2f}%"
          f"  ← 채점 기준은 이쪽 (우리가 매매하는 유니버스)")
    print(f"  간밤 미국 기준시각: {asof['asof'] if asof else '기록 없음'}")

    # ── ① read-across ────────────────────────────────────────────────
    print("\n  ① read-across — 간밤 미국에서 오른 테마가 오늘 한국에서 올랐나")
    print(f"  {'테마':<16}{'美 바스켓':>10}{'韓 오늘':>10}{'종목':>6}  판정")
    print("  " + "-" * 62)
    hit = miss = 0
    rows = []
    for theme, uc in sorted(us.items(), key=lambda kv: -kv[1]):
        tm = theme_move(theme)
        if not tm:
            continue
        kc, n = tm
        rows.append((uc, kc, theme, n))
        if uc > 0:
            ok = kc > base_mean
            hit, miss = (hit + 1, miss) if ok else (hit, miss + 1)
            verdict = "적중" if ok else "빗나감"
        else:
            verdict = "-"
        print(f"  {theme:<16}{uc:>+9.2f}%{kc:>+9.2f}%{n:>6}  {verdict}")

    up_us = [r for r in rows if r[0] > 0]
    dn_us = [r for r in rows if r[0] <= 0]
    if up_us:
        print(f"\n    미국 상승 테마 {len(up_us)}개 → 한국 중앙값 "
              f"{statistics.median(r[1] for r in up_us):+.2f}% "
              f"(기준선 {base_mean:+.2f}%) · 기준선 상회 {hit}/{hit + miss}")
    if dn_us:
        print(f"    미국 하락 테마 {len(dn_us)}개 → 한국 중앙값 "
              f"{statistics.median(r[1] for r in dn_us):+.2f}%")

    # ── ② 국내발 뉴스 ────────────────────────────────────────────────
    print("\n  ② 국내발 — 뉴스로 잡은 테마가 올랐나")
    got = False
    for theme, r in sorted(news.items(), key=lambda kv: -kv[1]["score"]):
        tm = theme_move(theme)
        if not tm or abs(r["score"]) < 1:
            continue
        got = True
        kc, n = tm
        tag = "호재" if r["score"] > 0 else "회피"
        ok = (kc > base_mean) if r["score"] > 0 else (kc < base_mean)
        print(f"  {theme:<16}{'뉴스 ' + tag:>10}{r['score']:>+7.1f}{kc:>+9.2f}%{n:>5}종목  "
              f"{'적중' if ok else '빗나감'}")
    if not got:
        print("    해당 없음")

    # ── ③ 픽 ────────────────────────────────────────────────────────
    print("\n  ③ 오늘의 픽 — 진입 트리거가 터졌나")
    if not picks:
        print("    픽 없음")
    for p in picks:
        m = moves.get(p["code"])
        if not m:
            print(f"    {p['name']}: 시세 없음")
            continue
        triggered = m["high"] >= p["entry"]
        hit_stop = m["low"] <= p["stop"]
        hit_t1 = m["high"] >= p["target1"]
        state = []
        if not triggered:
            state.append(f"미체결 (고가 {m['high']:,.0f} < 진입 {p['entry']:,.0f})")
        else:
            state.append(f"진입 {p['entry']:,.0f} 터치")
            if hit_stop:
                state.append("손절선 이탈")
            if hit_t1:
                state.append("1차 목표 도달")
            if not hit_stop and not hit_t1:
                state.append(f"보유 (종가 {m['close']:,.0f})")
        print(f"    {p['name']}({p['code']}) {m['chg_pct']:+.2f}%  ·  " + " · ".join(state))

    print("\n  ※ 하루치는 표본 1이다. 이걸로 임계값을 바꾸지 말 것 — 쌓아서 볼 것.")


if __name__ == "__main__":
    main()
