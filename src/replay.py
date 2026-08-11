"""되감기 복기 — 최근 N거래일을 하루씩 "그날 아침"으로 돌아가 새 규칙을 돌려본다.

매일 아침 실제 운영과 같은 순서로:
  ① 간밤 미국 테마 바스켓이 +3% 이상 오른 테마만 고른다 (검증 A: 크게 오른 것만 의미 있음)
  ② 그 테마 소속 종목을 전일까지의 일봉으로 판정 (A급 자리 = 3구간 충족 + 유동성 + 거래)
  ③ 진입 = 전일 고가 돌파 / 손절 = 종가 - 1.5ATR / 목표 = +2R
그리고 그날 이후 실제 봉으로 체결·손절·목표를 추적한다.

전일까지 데이터만 쓴다 — 판정하는 날의 봉은 절대 안 본다.

한계(정직하게): 테마→종목 지도가 7/6~8/7 급등 집계로 만들어졌으므로 그 기간과 겹치는
복기일엔 지도 자체에 미래 정보가 섞여 있다. 다만 1차 신호(미국 바스켓)와 자리 필터는
지도와 무관하게 계산된다.
"""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from datetime import datetime

import boot  # noqa: F401
import backtest
import prices_kr
import screen
from db import connect
from net import RunLog

US_MIN = 3.0          # 미국 바스켓 이 이상 오른 테마만 (검증 A 구간 기준)
HOLD = 20


def load_bars() -> dict[str, list[dict]]:
    """백테스트가 받아둔 일봉 캐시 재사용 — 네트워크 없이 돈다."""
    import json
    from net import CACHE_DIR
    best: dict[str, list[dict]] = {}
    for p in (CACHE_DIR / "ohlc").glob("*.json"):
        code = p.name.split("_")[0]
        try:
            bars = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        # 같은 종목의 캐시가 여러 개면 '가장 최근 날짜까지 있는 것'을 쓴다.
        # 길이로 고르면 옛날 파일이 이겨서 최근 거래일이 통째로 빠질 수 있다.
        cur = best.get(code)
        if not cur or bars[-1]["date"] > cur[-1]["date"] or (
                bars[-1]["date"] == cur[-1]["date"] and len(bars) > len(cur)):
            best[code] = bars
    return best


def refresh(bars_all: dict[str, list[dict]], codes: list[str], log: RunLog) -> None:
    """검증 대상 종목만 오늘까지 시세를 새로 받는다.

    백테스트 캐시에 기대면 백테스트를 안 돌린 날부터 복기가 과거에 멈춘다.
    이 함수 덕에 replay.py 하나만 돌려도 항상 최신까지 본다. 실패한 종목은
    캐시로 계속 가되 로그에 남긴다 — 사이클은 안 죽인다.
    """
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date as _date

    end = _date.today().strftime("%Y%m%d")
    stale = [c for c in codes
             if c not in bars_all or bars_all[c][-1]["date"] < end]

    def one(code):
        try:
            return code, prices_kr.fetch_ohlc(code, "20240701", end)
        except Exception:  # noqa: BLE001
            return code, None

    fails = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for code, rows in pool.map(one, stale):
            if rows and len(rows) > 80:
                bars_all[code] = rows
            elif code not in bars_all:
                fails += 1
    log.ok("replay", f"시세 갱신 {len(stale) - fails}/{len(stale)}종목"
                     + (f" (실패 {fails})" if fails else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--v2", action="store_true",
                    help="그림자 트랙: themes_v2.json의 신규 테마만 별도 채점 (계약 스코어와 분리)")
    ap.add_argument("--v3", action="store_true",
                    help="그림자 트랙: v1과 같은 신호·진입, 손절만 '10일 저가 밑'으로 교체 "
                         "(R3 검증: 승률 48.7→54.7%, 중앙값 -0.09→+0.15R)")
    args = ap.parse_args()
    log = RunLog()

    bars_all = load_bars()
    log.ok("replay", f"일봉 캐시 {len(bars_all)}종목")

    # 거래일 달력 = 삼성전자 봉
    cal = [b["date"] for b in bars_all["005930"]]
    days = cal[-args.days:]

    if args.v2:
        # 그림자 트랙 — v2 파일의 테마만. 계약(v1) 채점과 절대 섞이지 않는다.
        import json as _json
        from pathlib import Path as _P

        import tickers as _tk
        import verify_expansion
        cfg = _json.loads((_P(__file__).resolve().parent.parent / "config" /
                           "themes_v2.json").read_text(encoding="utf-8"))
        us_hist = {}
        members = defaultdict(list)
        names: dict[str, str] = {}
        for t in cfg["themes"]:
            us_hist[t["kr_theme"]] = verify_expansion.us_series(t["us_tickers"], log)
            for n in t["kr_stocks"]:
                c = _tk.to_code(n)
                if c:
                    members[t["kr_theme"]].append(c)
                    names[c] = n
                else:
                    log.warn("replay", f"v2 코드 미매칭: {n}")
        print("\n" + "▓" * 30 + "  V2 그림자 트랙 (신규 테마만)  " + "▓" * 30)
    elif args.v3:
        print("\n" + "▓" * 26 + "  V3 그림자 트랙 (v1 신호 + 구조 손절)  " + "▓" * 26)
        us_hist = backtest.us_theme_history(log)
        members = defaultdict(list)
        names = {}
        with connect() as conn:
            for r in conn.execute(
                    "SELECT code, name, themes FROM stocks WHERE themes IS NOT NULL"):
                names[r["code"]] = r["name"]
                for t in (x.strip() for x in r["themes"].split(",")):
                    if t in us_hist:
                        members[t].append(r["code"])
    else:
        us_hist = backtest.us_theme_history(log)

        members = defaultdict(list)
        names = {}
        with connect() as conn:
            for r in conn.execute(
                    "SELECT code, name, themes FROM stocks WHERE themes IS NOT NULL"):
                names[r["code"]] = r["name"]
                for t in (x.strip() for x in r["themes"].split(",")):
                    if t in us_hist:
                        members[t].append(r["code"])

    need = sorted({c for lst in members.values() for c in lst} | {"005930"})
    refresh(bars_all, need, log)

    # 달력도 갱신된 봉 기준으로 다시 잡는다
    cal = [b["date"] for b in bars_all["005930"]]
    days = cal[-args.days:]

    idx_of = {c: {b["date"]: i for i, b in enumerate(bs)} for c, bs in bars_all.items()}

    all_trades: list[dict] = []
    print("\n" + "=" * 84)
    print(f"되감기 복기 — 최근 {len(days)}거래일, 매일 '그날 아침' 시점으로 판정")
    print("=" * 84)

    for day in days:
        # ① 간밤 미국
        hot = []
        for theme, series in us_hist.items():
            chg = backtest.prior_us(series, day)
            if chg is not None and chg >= US_MIN:
                hot.append((theme, chg))
        hot.sort(key=lambda x: -x[1])

        d_disp = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        if not hot:
            print(f"\n▷ {d_disp}  간밤 미국 +{US_MIN}% 테마 없음 → 관망")
            continue

        # 그날 시장 기준선 (전 종목)
        daychg = []
        for c, bs in bars_all.items():
            i = idx_of[c].get(day)
            if i and i > 0 and bs[i - 1]["close"] > 0:
                daychg.append((bs[i]["close"] / bs[i - 1]["close"] - 1) * 100)
        base = statistics.median(daychg) if daychg else 0.0

        print(f"\n▷ {d_disp}  미국: " + ", ".join(f"{t} {c:+.1f}%" for t, c in hot[:4])
              + f"   (시장 중앙값 {base:+.2f}%)")

        # ② 자리 판정 — 전일까지 봉으로
        picks_today = []
        for theme, uchg in hot:
            for code in members[theme]:
                bs = bars_all.get(code)
                i = idx_of.get(code, {}).get(day)
                if not bs or i is None or i < 61:
                    continue
                ind = prices_kr.indicators(bs[:i])       # ← i 미포함 = 전일까지
                if not ind:
                    continue
                if ind["value20_eok"] < screen.MIN_VALUE_EOK:
                    continue
                if not ind["vol_ratio"] or ind["vol_ratio"] < 0.8:
                    continue
                plan = screen.make_plan(ind)
                if not plan["entry"] or plan.get("grade") != "A":
                    continue
                if args.v3:
                    # v3: 신호·진입은 v1과 동일, 손절만 구조(10일 저가 밑)로 교체.
                    s = min(b["low"] for b in bs[i - 10:i]) * 0.99
                    if s <= 0 or s >= plan["entry"]:
                        continue
                    plan = {**plan,
                            "stop": screen.round_tick(s),
                            "target1": screen.round_tick(plan["entry"] + 2 * (plan["entry"] - s)),
                            "target2": screen.round_tick(plan["entry"] + 3 * (plan["entry"] - s))}
                picks_today.append((code, theme, uchg, plan, i))

        if not picks_today:
            print("    A급 자리 없음 → 관망")
            continue

        # ③ 그날 이후 실제 추적
        for code, theme, uchg, plan, i in picks_today:
            bs = bars_all[code]
            entry, stop, t1 = plan["entry"], plan["stop"], plan["target1"]
            fwd = bs[i:i + HOLD]
            fill = None
            for k, b in enumerate(fwd[:3]):
                if b["high"] >= entry:
                    fill = max(entry, b["open"])
                    fidx = k
                    break
            status, r = "미체결", None
            if fill:
                risk = fill - stop
                status = "보유중"
                for b in fwd[fidx:]:
                    if b["low"] <= stop:
                        status, r = "손절", -1.0
                        break
                    if b["high"] >= t1:
                        status, r = "목표달성", round((t1 - fill) / risk, 2)
                        break
                if status == "보유중" and risk > 0:
                    r = round((bs[min(i + HOLD, len(bs)) - 1]["close"] - fill) / risk, 2)

            all_trades.append({"date": day, "status": status, "r": r})
            day0 = ""
            if idx_of[code].get(day) is not None and fill:
                b0 = bs[idx_of[code][day]]
                day0 = f"당일 {(b0['close'] / bs[idx_of[code][day] - 1]['close'] - 1) * 100:+.1f}%"
            rs = f"{r:+.2f}R" if r is not None else ""
            print(f"    {names.get(code, code):<14} [{theme}] 진입 {entry:>8,.0f} "
                  f"손절 {stop:>8,.0f} → {status:<5} {rs:<8} {day0}")

    # ── 집계 ──
    # 검증 계약(RESUME.md)은 2026-08-11부터 60거래일. 그 전 날짜는 참고용으로만 보인다.
    CONTRACT_START = "20260811"

    def agg(trades, label):
        filled = [t for t in trades if t["status"] != "미체결"]
        closed = [t for t in filled if t["status"] in ("손절", "목표달성")]
        wins = sum(1 for t in closed if t["status"] == "목표달성")
        rs = [t["r"] for t in filled if t["r"] is not None]
        line = (f"  {label}: 픽 {len(trades)} → 체결 {len(filled)} · "
                f"종결 {len(closed)} (목표 {wins}/손절 {len(closed) - wins})")
        if rs:
            line += f" · 평균 {statistics.mean(rs):+.2f}R"
        print(line)

    print("\n" + "=" * 84)
    contract = [t for t in all_trades if t["date"] >= CONTRACT_START]
    prior = [t for t in all_trades if t["date"] < CONTRACT_START]
    tag = ("▓ V2 그림자 스코어" if args.v2
           else "▓ V3 그림자 스코어 (구조 손절)" if args.v3
           else "★ 검증 계약 스코어")
    agg(contract, f"{tag} (2026-08-11~)")
    agg(prior, "  참고 — 계약 이전 (규칙 확정 전 구간 포함)")
    print("\n※ 테마 지도가 7/6~8/7 집계로 만들어져 그 기간 복기엔 지도에 미래 정보가 섞여 있다.")
    print("  미국 신호·자리 필터는 독립 계산. 8/8 이후 날짜가 가장 깨끗한 표본이다.")


if __name__ == "__main__":
    main()
