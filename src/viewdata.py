"""하루치 화면 데이터 조립. 리포트와 대시보드가 같은 걸 본다.

전부 DB에서 읽는다 — 그래야 과거 날짜 대시보드도 그대로 다시 그릴 수 있다.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import boot  # noqa: F401
from db import connect

WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]
BASELINE_ORDER = ["다우", "S&P500", "나스닥", "필라델피아 반도체",
                  "EWY(한국 ETF)", "원/달러", "코스피(전 거래일)", "코스닥(전 거래일)"]


def _next_trading_day(date: str) -> str:
    d = datetime.strptime(date, "%Y-%m-%d").date()
    nxt = d + timedelta(days=1) if d.weekday() >= 5 else d
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.isoformat()


def _map_insights(theme: str, chg: float | None, tickers: list[dict],
                  prev: float | None, preempted: bool) -> list[str]:
    """숫자에서 자동으로 뽑을 수 있는 해석 줄. 촉매(뉴스 이유)는 여기 없음 — 그건 사람 몫."""
    out: list[str] = []
    c = chg or 0

    # ① 흐름 — 어제 미국 세션과의 관계
    if prev is not None:
        if c <= -3 and prev >= 3:
            out.append(f"급등({prev:+.1f}%) 다음날 차익실현 반전 — 재료 소진 신호")
        elif c >= 3 and prev >= 3:
            out.append(f"이틀 연속 강세 ({prev:+.1f}% → {c:+.1f}%) — 추세 재료")
        elif c <= -3 and prev <= -3:
            out.append(f"이틀째 약세 ({prev:+.1f}% → {c:+.1f}%) — 하락 지속")
        elif c >= 3 and prev <= -3:
            out.append(f"급락({prev:+.1f}%) 후 반등 — 되돌림인지 반전인지 확인 필요")

    # ② 단독 vs 전체 — 바스켓 안에서 누가 움직였나
    if tickers:
        top = tickers[0]
        if top["chg"] >= 5 and c < 3:
            out.append(f"{top['ticker']} 단독 급등(+{top['chg']:.1f}%) — "
                       f"바스켓 미확인, 개별 재료 가능성")
        elif c >= 3 and all(t["chg"] > 0 for t in tickers):
            out.append("전 종목 동반 상승 — 테마 전체 재료")
        bottom = tickers[-1]
        if bottom["chg"] <= -5 and c > -3:
            out.append(f"{bottom['ticker']} 단독 급락({bottom['chg']:.1f}%) — 개별 악재 확인")

    # ③ 국내 선반영 — 갭 분해 검증(미국발 상승분은 시가에 소진)과 연결
    if preempted and c >= 3:
        out.append("국내는 이미 선반영 — 미국발 갭은 시가에 소진되는 경향, 시초 추격 주의")

    return out[:3]


def build(date: str, notes: list[str] | None = None) -> dict:
    with connect() as conn:
        regime_row = conn.execute(
            """SELECT change_pct ret5, close draw60 FROM global_baseline
               WHERE date=? AND symbol='KOSPI_REGIME'""", (date,)).fetchone()
        regime = (dict(regime_row) | {"caution": regime_row["ret5"] <= -3.0}
                  if regime_row else None)

        baseline = [dict(r) for r in conn.execute(
            """SELECT label, symbol, close, change_pct, asof FROM global_baseline
               WHERE date=? AND source='yahoo'""", (date,))]
        baseline.sort(key=lambda r: BASELINE_ORDER.index(r["label"])
                      if r["label"] in BASELINE_ORDER else 99)

        leaders: dict[str, list[dict]] = defaultdict(list)
        for r in conn.execute(
                """SELECT symbol, label, change_pct, close FROM global_baseline
                   WHERE date=? AND source='yahoo/leader' ORDER BY change_pct DESC""", (date,)):
            leaders[r["label"].replace(" 주도주", "")].append(
                {"ticker": r["symbol"], "chg": r["change_pct"], "close": r["close"]})

        us_move = {r["kr_theme"]: r["avg_change"] for r in conn.execute(
            "SELECT kr_theme, avg_change FROM theme_daily WHERE date=? AND source='us_basket'",
            (date,))}

        # 미국장 지도: 테마별 구성 종목 전체의 간밤 등락
        us_map: dict[str, list[dict]] = defaultdict(list)
        for r in conn.execute(
                """SELECT symbol, label, change_pct FROM global_baseline
                   WHERE date=? AND source='yahoo/ticker' ORDER BY change_pct DESC""", (date,)):
            us_map[r["label"]].append({"ticker": r["symbol"], "chg": r["change_pct"]})

        # 인사이트 재료 ①: 직전 미국 세션의 바스켓 등락 (흐름 해석용)
        prev_us = {r["kr_theme"]: r["avg_change"] for r in conn.execute(
            """SELECT kr_theme, avg_change FROM theme_daily
               WHERE source='us_basket' AND date=(
                 SELECT MAX(date) FROM theme_daily WHERE source='us_basket' AND date<?)""",
            (date,))}
        # 인사이트 재료 ②: 오늘 후보 중 '선반영' 플래그가 붙은 테마
        preempted = {r["kr_theme"] for r in conn.execute(
            """SELECT DISTINCT kr_theme FROM candidates
               WHERE date=? AND risk_flags LIKE '%선반영%'""", (date,))}

        cands = [dict(r) for r in conn.execute(
            "SELECT * FROM candidates WHERE date=? AND tier IN ('pick','pool') "
            "ORDER BY score DESC, name", (date,))]

        # 자리 완성 알림 (tier='watch') — 픽 아님, 테마 신호 대기 정보
        watch = [dict(r) for r in conn.execute(
            "SELECT * FROM candidates WHERE date=? AND tier='watch' ORDER BY name",
            (date,))]

        since = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=3)).date().isoformat()
        news = [dict(r) for r in conn.execute(
            """SELECT * FROM news_signals WHERE date>=? ORDER BY weight DESC, date DESC""",
            (since,))]

        surge = [dict(r) for r in conn.execute(
            """SELECT name, code, surge_days, themes FROM stocks
               WHERE surge_days >= 2 AND surge_asof IS NOT NULL
               ORDER BY surge_days DESC, name LIMIT 12""")]

        theme_mom = {r["kr_theme"]: r["d"] for r in conn.execute(
            """SELECT kr_theme, COUNT(DISTINCT date) d FROM theme_daily
               WHERE source='seed_xlsx' AND date >= date(?, '-14 day') GROUP BY kr_theme""",
            (date,))}

    # ── 테마 카드 조립 ──────────────────────────────────────────────────
    by_theme: dict[tuple, dict] = {}
    for c in cands:
        key = (c["origin"], c["kr_theme"])
        card = by_theme.setdefault(key, {
            "kr_theme": c["kr_theme"], "origin": c["origin"],
            "picks": [], "pool": [], "flags": set(),
            "us_change": us_move.get(c["kr_theme"]),
            "leaders": leaders.get(c["kr_theme"], []),
            "momentum_days": theme_mom.get(c["kr_theme"], 0),
        })
        (card["picks"] if c["tier"] == "pick" else card["pool"]).append(c)
        if c["risk_flags"]:
            card["flags"] |= set(c["risk_flags"].split(","))

    for card in by_theme.values():
        card["flags"] = sorted(card["flags"] - {"관리종목", "투자경고"})
        card["evidence"] = [n for n in news
                            if n["kr_theme"] == card["kr_theme"] and n["direction"] == "호재"][:2]

    overseas = sorted([c for c in by_theme.values() if c["origin"] == "readacross"],
                      key=lambda c: (-(len(c["picks"])), -(c["us_change"] or 0)))
    domestic = sorted([c for c in by_theme.values() if c["origin"] == "news"],
                      key=lambda c: (-(len(c["picks"])), -len(c["evidence"])))

    picks = [c for c in cands if c["tier"] == "pick"]

    # 회피 목록은 스크리너와 같은 기준을 쓴다 — 순점수가 음수인 테마만.
    # 호재·악재가 섞였지만 순점수가 플러스인 테마는 회피가 아니라 카드의 경고 배지로 나간다.
    # (안 그러면 "오늘의 픽"과 "회피"에 같은 테마가 동시에 뜬다)
    import collect_news_kr
    dead = defaultdict(list)
    for n in news:
        if n["direction"] in ("죽은테마", "악재"):
            dead[n["kr_theme"]].append(n)
    avoid = [{"kr_theme": r["kr_theme"], "score": round(r["score"], 1),
              "items": dead.get(r["kr_theme"], [])[:2]}
             for r in collect_news_kr.theme_scores(date) if r["score"] < 0]

    d = datetime.strptime(date, "%Y-%m-%d").date()
    target = _next_trading_day(date)
    td = datetime.strptime(target, "%Y-%m-%d").date()

    return {
        "run_date": date,
        "target_date": target,
        "target_label": f"{td.strftime('%Y.%m.%d')} {WEEKDAY[td.weekday()]}",
        "is_forward": target != date,
        "weekday": WEEKDAY[d.weekday()],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "regime": regime,
        "baseline": baseline,
        # 미국장 지도 + 검증 통계 기반 함의문. 임계값은 verify_themes.py 결과 그대로:
        # +5%↑ → 다음날 한국 상승확률 64.5% / +3~5% → 55.8% / -3%↓ → 26.7% (기준선 44.3%)
        "us_map": [
            {"kr_theme": t, "chg": us_move.get(t), "tickers": rows,
             "insights": _map_insights(t, us_move.get(t), rows,
                                       prev_us.get(t), t in preempted),
             "note": ("주도주 관심 — 검증상 상승확률 64.5%" if (us_move.get(t) or 0) >= 5 else
                      "관심 — 상승확률 55.8%" if (us_move.get(t) or 0) >= 3 else
                      "회피 — 상승확률 26.7%, 추격 금지" if (us_move.get(t) or 0) <= -3 else
                      "중립 — 신호 없음")}
            for t, rows in sorted(us_map.items(),
                                  key=lambda kv: -(us_move.get(kv[0]) or 0))
        ],
        "overseas": overseas,
        "domestic": domestic,
        "picks": picks,
        "watch": watch,
        "avoid": avoid,
        "surge": surge,
        "news": news,
        "notes": notes or [],
        "counts": {
            "picks": len(picks),
            "pool": sum(1 for c in cands if c["tier"] == "pool"),
            "themes": len(by_theme),
            "no_chart": sum(1 for c in cands if c["data_status"] != "ok"),
        },
    }


def headline(view: dict) -> list[str]:
    """아침 리포트용 3~5줄. 결론 박스와 같은 내용."""
    lines: list[str] = []

    sox = next((b for b in view["baseline"] if b["label"] == "필라델피아 반도체"), None)
    naz = next((b for b in view["baseline"] if b["label"] == "나스닥"), None)
    ewy = next((b for b in view["baseline"] if b["label"] == "EWY(한국 ETF)"), None)
    parts = [f"{b['label']} {b['change_pct']:+.2f}%" for b in (naz, sox, ewy) if b]
    lines.append("· 기준선: " + (" / ".join(parts) if parts else "데이터 부재 — 확인 필요"))

    rg = view.get("regime")
    if rg and rg["caution"]:
        lines.append(f"· ⚠ 시장 급락 국면 (코스피 5일 {rg['ret5']:+.1f}%) — "
                     "과거 이 국면에서 픽 기대값이 1/3로 준다. 오늘은 건너뛰거나 절반 비중 검토.")

    if view["overseas"]:
        top = view["overseas"][0]
        names = [p["name"] for p in top["picks"][:4]] or [p["name"] for p in top["pool"][:4]]
        lead = f" (美 {top['leaders'][0]['ticker']} {top['leaders'][0]['chg']:+.1f}%)" \
            if top["leaders"] else ""
        lines.append(f"· 해외발: {top['kr_theme']} 바스켓 {top['us_change']:+.1f}%{lead} "
                     f"→ {', '.join(names) if names else '국내 매칭 종목 없음'}")
    else:
        lines.append("· 해외발: 오른 테마 없음 — read-across 후보 없음")

    if view["domestic"]:
        top = view["domestic"][0]
        ev = top["evidence"][0]["title"][:38] + "…" if top["evidence"] else "근거 기사 없음"
        names = [p["name"] for p in top["picks"][:4]] or [p["name"] for p in top["pool"][:4]]
        lines.append(f"· 국내발: {top['kr_theme']} — \"{ev}\" → {', '.join(names) if names else '매칭 종목 없음'}")

    if view["picks"]:
        lines.append("· 오늘의 픽 " + str(len(view["picks"])) + "개: "
                     + ", ".join(f"{p['name']}({p['setup']})" for p in view["picks"][:4]))
    else:
        lines.append("· 오늘의 픽 없음 — 필터를 통과한 자리가 없다. 억지로 잡지 말 것.")

    if view["avoid"]:
        lines.append("· 회피: " + ", ".join(a["kr_theme"] for a in view["avoid"][:4]))
    return lines


if __name__ == "__main__":
    from datetime import date as _d
    v = build(_d.today().isoformat())
    print(f"{v['target_label']} 장전 · 픽 {v['counts']['picks']} / 풀 {v['counts']['pool']}\n")
    for line in headline(v):
        print(line)
