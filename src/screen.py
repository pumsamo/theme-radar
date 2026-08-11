"""후보 압축 + 매매계획 생성 (지시문 4-6b, 4-7).

핵심 원칙 두 가지:
  1. 테마 소속은 '풀'일 뿐이다. 필터를 통과한 것만 '오늘의 픽'으로 올린다.
  2. 진입가·손절선·목표가가 세트로 안 나오면 픽으로 내보내지 않는다.
     차트 데이터가 없으면 지어내지 말고 '차트 확인 필요'로 풀에만 둔다.
"""
from __future__ import annotations

import statistics
from datetime import date as _date
from datetime import datetime, timedelta

import boot  # noqa: F401
import collect_news_kr
import prices_kr
import themes_cfg
from db import connect
from net import RunLog

MIN_VALUE_EOK = 30.0     # 20일 평균 거래대금 하한(억) — 유동성
OVERHEAT = 1.30          # 20일선 이격 — 이 위는 추격 구간 (검증: -0.023R)
DRAW_FLOOR = -30.0       # 60일 고점 대비 이 밑은 탈락 (검증: -0.120R 이하, 승률 33%)
ATR_MULT = 1.5           # 손절폭 = 1.5 × ATR14 (위치 지도 검증에 쓴 값)
R1, R2 = 2.0, 3.0        # 목표 = 진입 + 2R / 3R

# 165,542건 위치 지도에서 가장 성적이 좋았던 구간 = '오늘의 픽' 자격
BEST_DRAW = (-15.0, -3.0)    # 60일 고점 대비 (+0.114R ~ +0.157R 구간)
BEST_EXT = (0.95, 1.20)      # 20일선 이격 (+0.088R ~ +0.090R 구간)
BEST_RSI = (45.0, 75.0)      # RSI (40 미만은 -0.065R로 확실히 나쁨)
MAX_THEMES = 8
# 테마당 시세를 조회할 종목 수. 반도체 소부장처럼 시드에 65종목인 테마가 있어서
# 이 값이 낮으면 아는 종목이 조용히 빠진다. 잘린 건 반드시 RunLog에 남겨 화면에 띄운다.
MAX_STOCKS_PER_THEME = 80


def round_tick(price: float) -> float:
    """KRX 호가단위로 정렬. 안 맞는 가격을 진입가로 주면 그대로 못 쓴다."""
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50),
                        (200_000, 100), (500_000, 500)):
        if price < limit:
            return round(price / tick) * tick
    return round(price / 1000) * 1000


def make_plan(ind: dict) -> dict:
    """일봉 지표 → 진입·손절·목표. 자리가 아니면 entry=None.

    규칙을 손으로 정하지 않고 165,542건 위치 지도(`bt_position.py`)가 가리킨 대로 짰다.
    이전 버전은 '정배열 돌파'와 '눌림목' 두 모양만 인정하고 역배열을 무조건 잘라냈는데,
    검증해 보니 배열은 성적을 거의 안 가르고(정배열 +0.042R vs 역배열 +0.029R)
    **고점 대비 얼마나 빠졌는지**가 갈랐다. 그래서 배열 조건을 버리고 위치로 판정한다.
    """
    close, atr = ind["close"], ind["atr"] or 0
    draw, ext = ind["from_hi60"], ind["ext_ma20"]

    if draw <= DRAW_FLOOR:
        return {"setup": "낙폭 과대", "entry": None,
                "note": f"60일 고점 대비 {draw:+.0f}% — 검증상 승률 33% 이하 구간"}
    if ext > OVERHEAT:
        return {"setup": "과열 추격 구간", "entry": None,
                "note": f"20일선 이격 {ext:.2f} — 눌림 기다릴 자리"}
    if not atr:
        return {"setup": "데이터 부족", "entry": None, "note": "ATR 계산 불가"}

    # 진입은 신호일 고가 돌파, 손절은 종가 기준 1.5 ATR — 검증에 쓴 규칙 그대로.
    entry = round_tick(ind["high"] * 1.002)
    stop = round_tick(close - ATR_MULT * atr)
    if stop <= 0 or stop >= entry:
        return {"setup": "손절선 불성립", "entry": None,
                "note": "변동성이 커서 손절폭이 진입가를 넘는다"}

    risk = entry - stop
    zone = []
    if BEST_DRAW[0] <= draw <= BEST_DRAW[1]:
        zone.append(f"고점 대비 {draw:+.0f}%")
    if BEST_EXT[0] <= ext <= BEST_EXT[1]:
        zone.append(f"이격 {ext:.2f}")
    if ind["rsi"] and BEST_RSI[0] <= ind["rsi"] <= BEST_RSI[1]:
        zone.append(f"RSI {ind['rsi']:.0f}")

    return {
        "setup": "A급 자리" if len(zone) >= 3 else f"B급 ({len(zone)}/3 충족)",
        "grade": "A" if len(zone) >= 3 else "B",
        "zone": zone,
        "entry": entry, "stop": stop,
        "target1": round_tick(entry + R1 * risk),
        "target2": round_tick(entry + R2 * risk),
        "rr": R1, "note": None,
    }


def _theme_stocks(themes: list[str]) -> tuple[dict[str, list[tuple[str, str, int]]], dict[str, int]]:
    """테마 → [(code, name, watchlist)] 와 테마별 잘려나간 종목 수.

    관심리스트 → 최근 등장순으로 자른다. 자른 건 숨기지 않고 개수를 돌려준다.
    """
    out: dict[str, list[tuple[str, str, int]]] = {}
    dropped: dict[str, int] = {}
    with connect() as conn:
        for theme in themes:
            total = conn.execute(
                "SELECT COUNT(*) c FROM stocks WHERE themes LIKE ?", (f"%{theme}%",)).fetchone()["c"]
            rows = conn.execute(
                """SELECT code, name, watchlist FROM stocks
                   WHERE themes LIKE ? ORDER BY watchlist DESC, last_seen DESC
                   LIMIT ?""", (f"%{theme}%", MAX_STOCKS_PER_THEME)).fetchall()
            out[theme] = [(r["code"], r["name"], r["watchlist"]) for r in rows]
            if total > len(rows):
                dropped[theme] = total - len(rows)
    return out, dropped


def pick_themes(date: str, log: RunLog) -> tuple[list[dict], list[dict]]:
    """오늘 볼 테마 = 해외발(미국 바스켓) + 국내발(뉴스). 죽은테마는 회피 목록으로 분리."""
    news = {r["kr_theme"]: r for r in collect_news_kr.theme_scores(date)}
    avoid = [r for r in news.values() if r["score"] < 0]
    avoid_names = {r["kr_theme"] for r in avoid}

    with connect() as conn:
        us_rows = conn.execute(
            """SELECT kr_theme, avg_change FROM theme_daily
               WHERE date=? AND source='us_basket' ORDER BY avg_change DESC""", (date,)).fetchall()
        # 최근 국내 모멘텀 (확인 층) — 신호가 아니라 가중치로만 쓴다
        since = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=10)).date().isoformat()
        mom = {r["kr_theme"]: r["d"] for r in conn.execute(
            """SELECT kr_theme, COUNT(DISTINCT date) d FROM theme_daily
               WHERE date>=? AND source='seed_xlsx' GROUP BY kr_theme""", (since,))}

    overseas = []
    for r in us_rows:
        if r["kr_theme"] in avoid_names or r["avg_change"] <= 0:
            continue
        overseas.append({
            "kr_theme": r["kr_theme"], "origin": "readacross",
            "us_change": r["avg_change"],
            "score": r["avg_change"] + 0.5 * mom.get(r["kr_theme"], 0),
            "momentum_days": mom.get(r["kr_theme"], 0),
        })
    overseas.sort(key=lambda x: x["score"], reverse=True)

    domestic = []
    for r in news.values():
        if r["score"] <= 0:
            continue
        domestic.append({
            "kr_theme": r["kr_theme"], "origin": "news",
            "news_score": round(r["score"], 1), "n_good": r["n_good"],
            "score": r["score"] + 0.5 * mom.get(r["kr_theme"], 0),
            "momentum_days": mom.get(r["kr_theme"], 0),
        })
    domestic.sort(key=lambda x: x["score"], reverse=True)

    log.ok("screen", f"해외발 {len(overseas)} · 국내발 {len(domestic)} · 회피 {len(avoid)}")
    return overseas[:MAX_THEMES], domestic[:MAX_THEMES]


def run(date: str, log: RunLog) -> dict:
    overseas, domestic = pick_themes(date, log)
    scores = collect_news_kr.theme_scores(date)
    avoid = [r for r in scores if r["score"] < 0]
    # 순점수는 플러스여도 죽은테마 기사가 섞여 있으면 픽에 경고를 단다 (추격 방지)
    dead_mixed = {r["kr_theme"] for r in scores if r["score"] >= 0 and r["n_dead"]}

    theme_origin: dict[str, str] = {}
    for t in domestic:
        theme_origin[t["kr_theme"]] = "news"
    for t in overseas:
        theme_origin.setdefault(t["kr_theme"], "readacross")

    stocks, dropped = _theme_stocks(list(theme_origin))
    codes = sorted({c for lst in stocks.values() for c, _, _ in lst})
    if not codes:
        log.warn("screen", "테마에 매칭된 종목이 없다")
        return {"picks": 0, "pool": 0}
    if dropped:
        detail = ", ".join(f"{t} {n}종목" for t, n in
                           sorted(dropped.items(), key=lambda kv: -kv[1])[:4])
        log.warn("screen", f"테마당 {MAX_STOCKS_PER_THEME}종목 상한으로 제외 — {detail} "
                           f"(총 {sum(dropped.values())}종목, 관심리스트 등록 종목은 항상 포함)")

    end = datetime.strptime(date, "%Y-%m-%d").strftime("%Y%m%d")
    start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=220)).strftime("%Y%m%d")
    log.ok("screen", f"일봉 조회 {len(codes)}종목")
    inds, bad = prices_kr.bulk_indicators(codes, start, end)
    if bad:
        log.warn("screen", f"시세 실패 {len(bad)}종목 — 해당 종목은 '차트 확인 필요'로 풀에만 둔다")

    risk = prices_kr.risk_lists()

    # 선반영 판정: 그 테마 종목들이 직전 거래일에 이미 올랐는가
    pre_moved: dict[str, float] = {}
    for theme, lst in stocks.items():
        chgs = [inds[c]["chg_pct"] for c, _, _ in lst if c in inds]
        if chgs:
            pre_moved[theme] = round(statistics.median(chgs), 2)

    rows: list[dict] = []
    for theme, lst in stocks.items():
        origin = theme_origin[theme]
        for code, name, watch in lst:
            ind = inds.get(code)
            flags = []
            if code in risk["관리종목"]:
                flags.append("관리종목")
            if code in risk["투자경고"]:
                flags.append("투자경고")
            if origin == "readacross" and pre_moved.get(theme, 0) >= 3.0:
                flags.append("선반영")
            if theme in dead_mixed:
                flags.append("죽은테마 신호 혼재")

            if not ind:
                rows.append({
                    "date": date, "code": code, "name": name, "kr_theme": theme,
                    "origin": origin, "tier": "pool", "reason": None, "setup": None,
                    "entry": None, "stop": None, "target1": None, "target2": None,
                    "rr": None, "score": 0.0, "risk_flags": ",".join(flags) or None,
                    "data_status": "차트 확인 필요",
                })
                continue

            plan = make_plan(ind)
            passed, failed = [], []

            if plan["entry"]:
                passed.append(plan["setup"])
            else:
                failed.append(plan.get("note") or plan["setup"])

            if ind["value20_eok"] >= MIN_VALUE_EOK:
                passed.append(f"거래대금 {ind['value20_eok']:,.0f}억")
            else:
                failed.append(f"유동성 미달({ind['value20_eok']:,.0f}억)")

            if ind["vol_ratio"] and ind["vol_ratio"] >= 0.8:
                passed.append(f"거래량 {ind['vol_ratio']:.1f}배")
            else:
                failed.append("거래 위축")

            # 손익비는 목표를 2R로 고정했으므로 항상 2.0 — 필터로 쓰지 않는다.
            # 대신 '자리 등급'으로 거른다: 검증에서 가장 좋았던 세 구간을 다 만족해야 픽.
            if plan["entry"]:
                if plan.get("grade") == "A":
                    passed.append(" · ".join(plan.get("zone", [])))
                else:
                    failed.append(f"자리 {plan['setup']}")

            hard_block = [f for f in flags if f in ("관리종목", "투자경고")]
            rr = plan.get("rr")
            is_pick = bool(plan["entry"]) and not failed and not hard_block

            score = 0.0
            if is_pick:
                score = (rr or 0) + (ind["vol_ratio"] or 0) + (1.0 if watch else 0.0)
                if "선반영" in flags:
                    score -= 1.0

            rows.append({
                "date": date, "code": code, "name": name, "kr_theme": theme,
                "origin": origin, "tier": "pick" if is_pick else "pool",
                "reason": " · ".join(passed if is_pick else failed[:2]) or None,
                "setup": plan["setup"],
                "entry": plan.get("entry"), "stop": plan.get("stop"),
                "target1": plan.get("target1"), "target2": plan.get("target2"),
                "rr": rr, "score": round(score, 2),
                "risk_flags": ",".join(flags) or None,
                "data_status": "ok",
            })

    with connect() as conn:
        conn.executemany(
            "UPDATE stocks SET surge_days=?, surge_asof=? WHERE code=?",
            [(ind["surge20"], ind["date"], code) for code, ind in inds.items()])
        conn.execute("DELETE FROM candidates WHERE date=?", (date,))
        conn.executemany(
            """INSERT OR REPLACE INTO candidates
               (date, code, name, kr_theme, origin, tier, reason, setup,
                entry, stop, target1, target2, rr, score, risk_flags, data_status)
               VALUES (:date,:code,:name,:kr_theme,:origin,:tier,:reason,:setup,
                       :entry,:stop,:target1,:target2,:rr,:score,:risk_flags,:data_status)""",
            rows)
        conn.commit()

    picks = sum(1 for r in rows if r["tier"] == "pick")
    log.ok("screen", f"픽 {picks} · 풀 {len(rows) - picks} (테마 {len(stocks)})")
    return {"picks": picks, "pool": len(rows) - picks, "avoid": avoid,
            "overseas": overseas, "domestic": domestic, "pre_moved": pre_moved}


if __name__ == "__main__":
    log = RunLog()
    today = _date.today().isoformat()
    res = run(today, log)

    with connect() as conn:
        print("\n── 오늘의 픽 ──")
        got = conn.execute(
            """SELECT * FROM candidates WHERE date=? AND tier='pick'
               ORDER BY score DESC LIMIT 15""", (today,)).fetchall()
        if not got:
            print("  없음 — 필터를 통과한 자리가 없다 (억지로 만들지 않는다)")
        for r in got:
            print(f"  {r['name']}({r['code']}) [{r['kr_theme']}] {r['setup']}")
            print(f"     진입 {r['entry']:,.0f} / 손절 {r['stop']:,.0f} / "
                  f"목표 {r['target1']:,.0f}·{r['target2']:,.0f} · 손익비 {r['rr']}")
            print(f"     통과: {r['reason']}  {('⚠ ' + r['risk_flags']) if r['risk_flags'] else ''}")

        print("\n── 풀 탈락 사유 상위 ──")
        for r in conn.execute(
                """SELECT reason, COUNT(*) n FROM candidates
                   WHERE date=? AND tier='pool' GROUP BY reason ORDER BY n DESC LIMIT 8""",
                (today,)):
            print(f"  {r['n']:>3}  {r['reason']}")
