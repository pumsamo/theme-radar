"""복기(백테스트) — 진입·손절 규칙에 실제로 edge가 있나 (지시문 Phase 3).

무엇을 재는가
  ① 기준선: 셋업 규칙(눌림목/정배열 + 손익비 1.5 + 유동성)만 전 종목에 돌린 성과
  ② 조건부: 간밤 미국 해당 테마 바스켓이 오른 날에만 잡았을 때의 성과
  ①과 ②의 차이가 read-across의 기여도다.

측정하지 못하는 것 (숫자에 안 들어감)
  뉴스 선행 층. RSS는 최근 기사만 줘서 과거 뉴스 신호를 복원할 수 없다.
  즉 여기 나오는 승률은 **뉴스 층을 뺀 시스템**의 성적이다.

보수적으로 잡은 가정 — 숫자를 좋아 보이게 만들지 않으려고
  · 진입은 실제로 트리거돼야 인정. 고가가 진입가에 닿지 않으면 미체결로 버린다.
  · 갭 상승 출발이면 진입가가 아니라 **시가**로 체결됐다고 본다(불리한 쪽).
  · 같은 봉에서 손절가와 목표가를 다 건드리면 **손절 먼저** 맞은 걸로 본다.
  · 20거래일 안에 둘 다 안 닿으면 그날 종가로 청산(시간 손절).
  · 수수료·세금·슬리피지는 빼지 않았다 — 실제 성적은 이보다 낮다.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path

import boot  # noqa: F401
import prices_kr
import screen
import themes_cfg
import tickers
from db import connect
from net import CACHE_DIR, RunLog, fetch_json

OHLC_CACHE = CACHE_DIR / "ohlc"
YF = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3y&interval=1d"

HOLD_BARS = 20        # 최대 보유 거래일
ENTRY_WINDOW = 3      # 진입 트리거 유효 기간(거래일)
HORIZONS = (3, 5, 10, 20)

# 스팩·리츠·우선주는 셋업 규칙 대상이 아니다
EXCLUDE_NAME = re.compile(r"(스팩|기업인수목적|리츠)")


def universe(kind: str, limit: int | None) -> list[tuple[str, str]]:
    if kind == "seed":
        with connect() as conn:
            rows = [(r["code"], r["name"]) for r in
                    conn.execute("SELECT code, name FROM stocks ORDER BY code")]
    else:
        tbl = tickers.table()
        rows = [(v["code"], k) for k, v in tbl.items() if v["market"] in ("KOSPI", "KOSDAQ")]
        rows.sort(key=lambda x: x[0])
    rows = [(c, n) for c, n in rows if not EXCLUDE_NAME.search(n) and re.fullmatch(r"\d{6}", c)]
    return rows[:limit] if limit else rows


def history(code: str, start: str, end: str) -> list[dict]:
    """일봉 전체 기간. 디스크 캐시 — 두 번째 실행부터는 네트워크를 안 탄다."""
    OHLC_CACHE.mkdir(parents=True, exist_ok=True)
    path = OHLC_CACHE / f"{code}_{start}_{end}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    rows = prices_kr.fetch_ohlc(code, start, end)
    path.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def load_all(codes: list[str], start: str, end: str, log: RunLog) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    fails = 0

    def one(code):
        try:
            return code, history(code, start, end)
        except Exception:  # noqa: BLE001
            return code, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, (code, rows) in enumerate(pool.map(one, codes), 1):
            if rows and len(rows) > 80:
                out[code] = rows
            else:
                fails += 1
            if i % 300 == 0:
                log.ok("bt", f"일봉 {i}/{len(codes)}")
    log.ok("bt", f"일봉 확보 {len(out)}종목 (실패·데이터부족 {fails})")
    return out


# ── 미국 테마 히스토리 ────────────────────────────────────────────────────

def us_theme_history(log: RunLog) -> dict[str, dict[str, float]]:
    """{kr_theme: {US세션날짜: 바스켓 중앙값 등락률}}"""
    themes = themes_cfg.readacross_themes()
    tick_hist: dict[str, dict[str, float]] = {}

    def one(sym: str):
        try:
            data = fetch_json(YF.format(sym=sym.replace("^", "%5E")), timeout=25)
            res = data["chart"]["result"][0]
            ts = res["timestamp"]
            closes = res["indicators"]["quote"][0]["close"]
            series: dict[str, float] = {}
            prev = None
            for t, c in zip(ts, closes):
                if c is None:
                    continue
                d = datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
                if prev:
                    series[d] = (c / prev - 1) * 100
                prev = c
            return sym, series
        except Exception:  # noqa: BLE001
            return sym, None

    syms = sorted({t for th in themes for t in th["us_tickers"]})
    with ThreadPoolExecutor(max_workers=5) as pool:
        for sym, series in pool.map(one, syms):
            if series:
                tick_hist[sym] = series

    out: dict[str, dict[str, float]] = {}
    for th in themes:
        got = [tick_hist[t] for t in th["us_tickers"] if t in tick_hist]
        if not got:
            continue
        dates = set().union(*(set(g) for g in got))
        out[th["kr_theme"]] = {
            d: statistics.median([g[d] for g in got if d in g])
            for d in dates if any(d in g for g in got)
        }
    log.ok("bt", f"미국 테마 히스토리 {len(out)}개 (티커 {len(tick_hist)}/{len(syms)})")
    return out


def prior_us(theme_series: dict[str, float], kr_date: str) -> float | None:
    """한국 거래일 D 직전에 끝난 미국 세션의 등락률."""
    for back in range(1, 6):
        d = (datetime.strptime(kr_date, "%Y%m%d").date() - timedelta(days=back)).isoformat()
        if d in theme_series:
            return theme_series[d]
    return None


# ── 시뮬레이션 ───────────────────────────────────────────────────────────

def simulate(rows: list[dict], i: int, plan: dict) -> dict | None:
    """봉 i 종가 기준으로 세운 계획을 i+1부터 실제로 굴려본다."""
    entry, stop, t1 = plan["entry"], plan["stop"], plan["target1"]
    fwd = rows[i + 1: i + 1 + HOLD_BARS + ENTRY_WINDOW]
    if len(fwd) < HOLD_BARS:
        return None

    fill = None
    for k, bar in enumerate(fwd[:ENTRY_WINDOW]):
        if bar["high"] >= entry:
            # 시가가 이미 진입가 위면 그 시가로 체결 — 불리한 쪽으로 잡는다
            fill = max(entry, bar["open"])
            fill_idx = k
            break
    if fill is None:
        return {"entered": False}

    risk = fill - stop
    if risk <= 0:
        return {"entered": False}

    result, exit_px, bars_held = None, None, 0
    for k, bar in enumerate(fwd[fill_idx: fill_idx + HOLD_BARS], start=1):
        bars_held = k
        if bar["low"] <= stop:            # 같은 봉에서 둘 다 닿으면 손절 우선
            result, exit_px = "loss", stop
            break
        if bar["high"] >= t1:
            result, exit_px = "win", t1
            break
    if result is None:
        result = "timeout"
        exit_px = fwd[fill_idx + HOLD_BARS - 1]["close"]

    horizon = {}
    for h in HORIZONS:
        seg = fwd[fill_idx: fill_idx + h]
        if len(seg) == h:
            horizon[h] = round((seg[-1]["close"] / fill - 1) * 100, 2)

    return {
        "entered": True, "result": result,
        "r": round((exit_px - fill) / risk, 3),
        "ret_pct": round((exit_px / fill - 1) * 100, 2),
        "bars": bars_held,
        "max_gain": round((max(b["high"] for b in fwd[fill_idx:fill_idx + HOLD_BARS]) / fill - 1) * 100, 2),
        "max_draw": round((min(b["low"] for b in fwd[fill_idx:fill_idx + HOLD_BARS]) / fill - 1) * 100, 2),
        "horizon": horizon,
    }


def run(kind: str, limit: int | None, start: str, end: str, log: RunLog) -> list[dict]:
    codes = universe(kind, limit)
    log.ok("bt", f"유니버스 {len(codes)}종목 ({kind})")
    hist = load_all([c for c, _ in codes], start, end, log)
    name_of = dict(codes)

    with connect() as conn:
        stock_themes = {r["code"]: [t.strip() for t in (r["themes"] or "").split(",") if t.strip()]
                        for r in conn.execute("SELECT code, themes FROM stocks")}

    us_hist = us_theme_history(log)
    trades: list[dict] = []

    for code, rows in hist.items():
        # 지표에 60봉, 앞으로 굴릴 23봉이 필요하다
        for i in range(60, len(rows) - (HOLD_BARS + ENTRY_WINDOW) - 1):
            ind = prices_kr.indicators(rows[:i + 1])
            if not ind:
                continue
            if ind["value20_eok"] < screen.MIN_VALUE_EOK:
                continue
            if not ind["vol_ratio"] or ind["vol_ratio"] < 0.8:
                continue
            plan = screen.make_plan(ind)
            # 자격 구간을 몇 개 만족해야 픽으로 올릴지를 데이터로 정하려고,
            # 여기서는 자르지 않고 충족 개수(zones)를 기록만 해 둔다.
            if not plan["entry"]:
                continue

            sim = simulate(rows, i, plan)
            if not sim:
                continue

            themes = stock_themes.get(code, [])
            us_chg = None
            for t in themes:
                if t in us_hist:
                    v = prior_us(us_hist[t], rows[i]["date"])
                    if v is not None:
                        us_chg = v if us_chg is None else max(us_chg, v)

            trades.append({
                "code": code, "name": name_of.get(code, code), "date": rows[i]["date"],
                "setup": plan["setup"], "rr_plan": plan["rr"],
                "zones": len(plan.get("zone", [])),
                "entry": plan["entry"], "stop": plan["stop"], "target1": plan["target1"],
                "us_theme_chg": us_chg, "themes": themes, **sim,
            })

    log.ok("bt", f"셋업 {len(trades)}건 (체결 {sum(1 for t in trades if t['entered'])})")
    return trades


# ── 집계 ────────────────────────────────────────────────────────────────

def stats(trades: list[dict], label: str) -> dict:
    filled = [t for t in trades if t.get("entered")]
    if not filled:
        return {"label": label, "n": 0}
    wins = [t for t in filled if t["result"] == "win"]
    losses = [t for t in filled if t["result"] == "loss"]
    rs = [t["r"] for t in filled]
    out = {
        "label": label,
        "n_setup": len(trades),
        "n": len(filled),
        "fill_rate": round(len(filled) / len(trades) * 100, 1),
        "win": round(len(wins) / len(filled) * 100, 1),
        "loss": round(len(losses) / len(filled) * 100, 1),
        "timeout": round(sum(1 for t in filled if t["result"] == "timeout") / len(filled) * 100, 1),
        "avg_r": round(statistics.mean(rs), 3),
        "median_r": round(statistics.median(rs), 3),
        "avg_ret": round(statistics.mean(t["ret_pct"] for t in filled), 2),
        "avg_bars": round(statistics.mean(t["bars"] for t in filled), 1),
        "max_draw": round(statistics.mean(t["max_draw"] for t in filled), 2),
    }
    for h in HORIZONS:
        vals = [t["horizon"][h] for t in filled if h in t["horizon"]]
        out[f"d{h}"] = round(statistics.mean(vals), 2) if vals else None
    return out


def show(s: dict) -> None:
    if not s.get("n"):
        print(f"  {s['label']}: 표본 없음")
        return
    print(f"\n  ▸ {s['label']}")
    print(f"    셋업 {s['n_setup']:,}건 → 체결 {s['n']:,}건 (체결률 {s['fill_rate']}%)")
    print(f"    승 {s['win']}% / 패 {s['loss']}% / 시간청산 {s['timeout']}%")
    print(f"    평균 {s['avg_r']:+.3f}R · 중앙값 {s['median_r']:+.3f}R · 평균수익 {s['avg_ret']:+.2f}%")
    print(f"    평균 보유 {s['avg_bars']}일 · 평균 최대낙폭 {s['max_draw']:+.2f}%")
    print(f"    보유일 수익률  3일 {s['d3']:+.2f}% · 5일 {s['d5']:+.2f}% · "
          f"10일 {s['d10']:+.2f}% · 20일 {s['d20']:+.2f}%")


def save_outcomes(trades: list[dict]) -> int:
    rows = []
    for t in trades:
        if not t.get("entered"):
            continue
        d = datetime.strptime(t["date"], "%Y%m%d").date().isoformat()
        for h, ret in t["horizon"].items():
            rows.append((d, t["code"], h, ret, t["max_gain"], t["max_draw"],
                         1 if t["result"] == "loss" else 0))
    with connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO outcomes
               (date, code, horizon, ret_pct, max_gain, max_draw, hit_stop)
               VALUES (?,?,?,?,?,?,?)""", rows)
        conn.commit()
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="셋업 규칙 복기")
    ap.add_argument("--universe", choices=["all", "seed"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="종목 수 제한(빠른 확인용)")
    ap.add_argument("--start", default=(_date.today() - timedelta(days=760)).strftime("%Y%m%d"))
    ap.add_argument("--end", default=_date.today().strftime("%Y%m%d"))
    args = ap.parse_args()

    log = RunLog()
    trades = run(args.universe, args.limit, args.start, args.end, log)
    if not trades:
        raise SystemExit("셋업이 하나도 안 잡혔다 — 기간·유니버스 확인")

    print("\n" + "=" * 66)
    print(f"복기 결과  {args.start} ~ {args.end}  ({args.universe} 유니버스)")
    print("=" * 66)

    show(stats(trades, "① 기준선 — 셋업 규칙만"))

    tagged = [t for t in trades if t["us_theme_chg"] is not None]
    if tagged:
        show(stats([t for t in tagged if t["us_theme_chg"] > 0],
                   "② 간밤 미국 테마 상승일에만"))
        show(stats([t for t in tagged if t["us_theme_chg"] <= 0],
                   "③ (대조군) 미국 테마 하락일"))
    else:
        print("\n  미국 테마가 매핑된 셋업이 없어 ②③은 비교 불가")

    print("\n  ▸ 자격 구간 충족 개수별 — 픽 기준선을 어디에 그을지")
    for z in (0, 1, 2, 3):
        show(stats([t for t in trades if t.get("zones") == z], f"  {z}/3 충족"))

    print("\n  ▸ 충족 개수 × 미국 테마 상승일 (실제 운영 조건)")
    for z in (2, 3):
        sel = [t for t in trades if t.get("zones") == z
               and t["us_theme_chg"] is not None and t["us_theme_chg"] > 0]
        show(stats(sel, f"  {z}/3 + 미국 상승"))

    n = save_outcomes(trades)
    print(f"\noutcomes 테이블에 {n:,}행 기록")
    print("주의: 수수료·세금·슬리피지 미반영. 뉴스 선행 층은 복기 대상에서 빠져 있다.")
