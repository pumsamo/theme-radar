"""간밤 미국장 → 글로벌 기준선 + 테마별 등락 (지시문 4-1, 4-2).

"미국장만 보고 한국장 방향을 말하지 말 것" — 그래서 결론보다 기준선을 먼저 수집한다.
테마 등락은 개별 movers 스크래핑 대신 **테마 바스켓**(config/themes.json의 us_tickers)의
중앙값으로 계산한다. 스크래핑보다 안 깨지고, 어차피 read-across는 테마 단위라 이게 맞다.
"""
from __future__ import annotations

import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boot  # noqa: F401
import themes_cfg
from db import connect
from net import RunLog, fetch_json

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
HIST = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"   # holiday_gap 전용
HOLIDAYS = Path(__file__).resolve().parent.parent / "config" / "holidays_kr.json"

BASELINE = [
    ("^DJI", "다우"),
    ("^GSPC", "S&P500"),
    ("^IXIC", "나스닥"),
    ("^SOX", "필라델피아 반도체"),
    ("EWY", "EWY(한국 ETF)"),
    ("KRW=X", "원/달러"),
    ("^KS11", "코스피(전 거래일)"),
    ("^KQ11", "코스닥(전 거래일)"),
]

# 코스피200 야간선물은 공개 무료 소스가 없다. 지어내지 않고 부재로 표기한다(지시문 1번).
# EWY(미국 상장 한국 ETF)가 간밤 한국물 심리를 보는 대용치 — 선물 대신 이걸 본다.
MISSING_NOTE = "코스피200 야간선물: 무료 소스 없음 → EWY로 대체 판단"


def quote(symbol: str) -> dict:
    """마지막 종가와 직전 종가로 등락률. 실패하면 예외를 올려 호출부에서 격리한다."""
    data = fetch_json(CHART.format(sym=symbol.replace("^", "%5E")), timeout=15)
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"{symbol}: 응답에 result 없음")
    res = result[0]
    meta = res.get("meta", {})
    closes = [c for c in (res["indicators"]["quote"][0].get("close") or []) if c is not None]
    if len(closes) < 2:
        raise RuntimeError(f"{symbol}: 종가 데이터 부족")

    last, prev = closes[-1], closes[-2]
    ts = meta.get("regularMarketTime")
    asof = (datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")
            if ts else None)
    return {
        "symbol": symbol,
        "close": round(last, 2),
        "change_pct": round((last / prev - 1) * 100, 2),
        "asof": asof,
        "currency": meta.get("currency"),
    }


def _quote_safe(symbol: str) -> tuple[str, dict | None, str | None]:
    try:
        return symbol, quote(symbol), None
    except Exception as exc:  # noqa: BLE001
        return symbol, None, f"{type(exc).__name__}: {exc}"


def _bulk(symbols: list[str], workers: int = 6) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sym, data, err in pool.map(_quote_safe, symbols):
            out[sym] = data
            if err:
                out.setdefault("_errors", {})  # type: ignore[assignment]
    return out


def baseline(date: str, log: RunLog) -> list[dict]:
    """미국 3대 지수·SOX·EWY·원달러·코스피200 스냅샷."""
    rows: list[dict] = []
    results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, data, err in pool.map(_quote_safe, [s for s, _ in BASELINE]):
            results[sym] = (data, err)

    with connect() as conn:
        for sym, label in BASELINE:
            data, err = results[sym]
            if not data:
                log.warn("us/baseline", f"{label}({sym}) 수신 실패 — 데이터 부재로 표기: {err}")
                rows.append({"symbol": sym, "label": label, "close": None,
                             "change_pct": None, "asof": None, "status": "데이터 부재"})
                continue
            conn.execute(
                """INSERT OR REPLACE INTO global_baseline
                   (date, symbol, label, close, change_pct, asof, source)
                   VALUES (?,?,?,?,?,?,'yahoo')""",
                (date, sym, label, data["close"], data["change_pct"], data["asof"]))
            rows.append({**data, "label": label, "status": "ok"})
        conn.commit()

    ok = sum(1 for r in rows if r["status"] == "ok")
    log.ok("us/baseline", f"{ok}/{len(BASELINE)}개 수집")
    return rows


def theme_moves(date: str, log: RunLog) -> list[dict]:
    """미국 테마 바스켓 등락 → 상위 테마와 주도주."""
    themes = themes_cfg.readacross_themes()
    symbols = sorted({t for th in themes for t in th["us_tickers"]})

    quotes: dict[str, dict | None] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, data, err in pool.map(_quote_safe, symbols):
            quotes[sym] = data
            if err:
                errors[sym] = err
    if errors:
        log.warn("us/theme", f"티커 {len(errors)}개 수신 실패: "
                             + ", ".join(sorted(errors)[:8]))

    out: list[dict] = []
    with connect() as conn:
        for th in themes:
            got = [(t, quotes[t]["change_pct"]) for t in th["us_tickers"]
                   if quotes.get(t)]
            if not got:
                log.warn("us/theme", f"{th['kr_theme']}: 바스켓 전부 실패 — 스킵")
                continue
            changes = [c for _, c in got]
            med = round(statistics.median(changes), 2)
            leaders = sorted(got, key=lambda x: x[1], reverse=True)
            out.append({
                "kr_theme": th["kr_theme"],
                "us_theme": th.get("us_theme"),
                "change_pct": med,
                "n_ok": len(got),
                "n_total": len(th["us_tickers"]),
                "leaders": [{"ticker": t, "chg": c} for t, c in leaders[:3]],
            })
            conn.execute(
                """INSERT OR REPLACE INTO theme_daily
                   (date, kr_theme, n_stocks, avg_change, source)
                   VALUES (?,?,?,?,'us_basket')""",
                (date, th["kr_theme"], len(got), med))
            # 주도주는 대시보드에서 "美 COHR +13.4%" 같은 근거로 쓴다.
            # 테이블을 늘리지 않으려고 global_baseline(글로벌 시세 스냅샷)에 함께 둔다.
            for tk, chg in leaders[:3]:
                conn.execute(
                    """INSERT OR REPLACE INTO global_baseline
                       (date, symbol, label, close, change_pct, asof, source)
                       VALUES (?,?,?,?,?,?,'yahoo/leader')""",
                    (date, tk, f"{th['kr_theme']} 주도주", quotes[tk]["close"], chg,
                     quotes[tk]["asof"]))
            # '미국장 지도' 섹션용 — 바스켓 구성 전 종목의 등락을 남긴다
            for tk, chg in got:
                conn.execute(
                    """INSERT OR REPLACE INTO global_baseline
                       (date, symbol, label, close, change_pct, asof, source)
                       VALUES (?,?,?,?,?,?,'yahoo/ticker')""",
                    (date, tk, th["kr_theme"], quotes[tk]["close"], chg,
                     quotes[tk]["asof"]))
        conn.commit()

    out.sort(key=lambda r: r["change_pct"], reverse=True)
    log.ok("us/theme", f"테마 {len(out)}개 · 선두 "
                       + ", ".join(f"{r['kr_theme']} {r['change_pct']:+.1f}%" for r in out[:3]))
    return out


def market_regime(date: str, log: RunLog) -> dict | None:
    """코스피 국면 스냅샷 — 브리핑의 '정보 줄' 전용. 픽 규칙에는 쓰지 않는다(검증 동결).

    근거(bt_regime.py, 체결 55,641건): 코스피 5일 수익률 -3% 이하 국면에서
    기대값이 +0.154R → +0.053R로 1/3토막. 60일 고점 대비 -10% 이하(깊은 조정)는
    오히려 +0.253R로 최상 — '떨어진' 시장이 아니라 '떨어지는 중'인 시장이 위험하다.
    """
    try:
        data = fetch_json(CHART.format(sym="%5EKS11").replace("range=5d", "range=3mo"),
                          timeout=20)
        closes = [c for c in data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                  if c is not None]
        if len(closes) < 21:
            raise RuntimeError("봉 부족")
    except Exception as exc:  # noqa: BLE001
        log.warn("us/regime", f"코스피 국면 계산 실패 — 표시 생략: {exc}")
        return None

    ret5 = (closes[-1] / closes[-6] - 1) * 100
    draw60 = (closes[-1] / max(closes[-60:]) - 1) * 100
    regime = {
        "ret5": round(ret5, 2),
        "draw60": round(draw60, 2),
        "caution": ret5 <= -3.0,   # 검증상 기대값 1/3 구간
    }
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO global_baseline
               (date, symbol, label, close, change_pct, asof, source)
               VALUES (?,'KOSPI_REGIME',?,?,?,NULL,'yahoo/regime')""",
            (date, f"5일 {ret5:+.1f}% · 60일고점 {draw60:+.1f}%",
             regime["draw60"], regime["ret5"]))
        conn.commit()
    log.ok("us/regime", f"코스피 5일 {ret5:+.1f}% · 고점대비 {draw60:+.1f}%"
                        + (" ⚠ 급락 국면" if regime["caution"] else ""))
    return regime


def _kr_prev_trading_day(date: str) -> str:
    """date 직전의 한국 거래일 (주말·config/holidays_kr.json 제외)."""
    hol = json.loads(HOLIDAYS.read_text(encoding="utf-8"))["dates"]
    d = _date.fromisoformat(date) - timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in hol:
        d -= timedelta(days=1)
    return d.isoformat()


def _history(symbol: str) -> dict[str, float]:
    """최근 1개월 일봉 종가 {미국 세션 날짜: 종가}. 실패하면 빈 dict."""
    try:
        res = fetch_json(HIST.format(sym=symbol.replace("^", "%5E")), timeout=15)["chart"]["result"][0]
        return {datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"): c
                for ts, c in zip(res.get("timestamp") or [], res["indicators"]["quote"][0].get("close") or [])
                if c is not None}
    except Exception:  # noqa: BLE001
        return {}


def holiday_gap(date: str, log: RunLog, store: bool = True) -> dict | None:
    """휴장 뒤 첫 거래일 표시 — 한국이 쉬는 동안 미국장이 2거래일 이상 열렸으면 그 누적 등락을 남긴다.

    브리핑의 '정보 줄' 전용이다. 픽 규칙(해외발 = 마지막 미국 세션 하루 등락)에는 쓰지 않는다(검증 동결).
    배경(2026-10-02 확인): quote()는 마지막 종가 ÷ 직전 종가라 한국이 며칠 쉬어도 마지막 하루만 본다.
      추석 뒤 9/28 아침 — 하루 기준으론 미국 테마 13개 중 8개가 플러스였지만 한국이 쉰 3거래일(9/23~25) 누적으론
      2개뿐이었다 (광통신 하루 +0.9% vs 누적 −3.9%, EWY 하루 +2.6% vs 누적 −2.8%). 그날 한국장은 누적 쪽으로 움직였다.
    평소(주말 포함)에는 한국이 못 본 미국 세션이 1개라 None을 돌려주고 아무것도 남기지 않는다.
    """
    kr_prev = _kr_prev_trading_day(date)
    spx = _history("^GSPC")
    unseen = sorted(d for d in spx if kr_prev <= d < date)   # 한국 직전 거래일 장 마감 뒤에 열린 미국 세션들
    if len(unseen) < 2:
        return None
    seen = sorted(d for d in spx if d < kr_prev)
    if not seen:
        raise RuntimeError("휴장 전 미국 종가를 찾지 못함")
    base, last, prev = seen[-1], unseen[-1], unseen[-2]

    themes = themes_cfg.readacross_themes()
    heads = [("^IXIC", "나스닥"), ("^SOX", "필라델피아 반도체"), ("EWY", "EWY(한국 ETF)")]
    symbols = [s for s, _ in heads] + sorted({t for th in themes for t in th["us_tickers"]})
    with ThreadPoolExecutor(max_workers=6) as pool:
        hist = dict(zip(symbols, pool.map(_history, symbols)))

    def chg(sym: str, a: str, b: str) -> float | None:
        h = hist.get(sym) or {}
        return (h[a] / h[b] - 1) * 100 if a in h and b in h else None

    base_rows = [(lab, chg(sym, last, base)) for sym, lab in heads]
    rows = []
    for th in themes:
        one = [x for x in (chg(t, last, prev) for t in th["us_tickers"]) if x is not None]
        cum = [x for x in (chg(t, last, base) for t in th["us_tickers"]) if x is not None]
        if one and cum:
            rows.append((th["kr_theme"], statistics.median(one), statistics.median(cum)))
    flips = sorted((r for r in rows if (r[1] > 0) != (r[2] > 0)), key=lambda r: -abs(r[2] - r[1]))

    days = "·".join(f"{int(d[5:7])}/{int(d[8:])}" for d in unseen)
    label = (f"한국 휴장 중 미국 {len(unseen)}거래일({days}) 누적: "
             + " / ".join(f"{lab} {v:+.2f}%" for lab, v in base_rows if v is not None))
    if rows:
        label += (f" · 미국 테마 {len(rows)}개 중 플러스 — 하루 기준 {sum(1 for r in rows if r[1] > 0)}개, "
                  f"누적 기준 {sum(1 for r in rows if r[2] > 0)}개")
    if flips:
        label += " · 방향이 갈린 테마: " + ", ".join(f"{t}(하루 {o:+.1f}% → 누적 {c:+.1f}%)" for t, o, c in flips[:4])
    nasdaq = base_rows[0][1]
    if store:
        with connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO global_baseline
                   (date, symbol, label, close, change_pct, asof, source)
                   VALUES (?,'HOLIDAY_GAP',?,?,?,NULL,'yahoo/holiday')""",
                (date, label, len(unseen), None if nasdaq is None else round(nasdaq, 2)))
            conn.commit()
    log.ok("us/holiday", label)
    return {"sessions": unseen, "base": base, "label": label, "themes": rows, "flips": flips}


if __name__ == "__main__":
    from datetime import date as _d
    log = RunLog()
    today = _d.today().isoformat()

    print("── 글로벌 기준선 ──")
    for r in baseline(today, log):
        if r["status"] == "ok":
            print(f"  {r['label']:<18} {r['close']:>10,.2f}  {r['change_pct']:+6.2f}%  ({r['asof']})")
        else:
            print(f"  {r['label']:<18} {'데이터 부재':>12}")

    print("\n── 미국 테마 (바스켓 중앙값) ──")
    for r in theme_moves(today, log):
        lead = " / ".join(f"{x['ticker']} {x['chg']:+.1f}%" for x in r["leaders"])
        print(f"  {r['change_pct']:+6.2f}%  {r['kr_theme']:<16} [{r['n_ok']}/{r['n_total']}]  {lead}")
