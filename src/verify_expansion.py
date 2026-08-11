"""바스켓 확충안 검증 — 구멍을 메우면 실제로 좋아지는가 (과거 2년).

확충안 (8/12 인포그래픽 교차검증에서 나온 것):
  ① AI 소프트웨어 바스켓: PLTR·AI·SNOW·TEAM → + ORCL·NOW·CRM·DDOG
  ② 신규 테마 '사이버보안': 미국 PANW·CRWD·ZS·FTNT ↔ 국내 보안주
  ③ 신규 테마 '에너지·정유': 미국 XOM·CVX·COP ↔ 국내 정유주

측정: 간밤 미국 바스켓 등락 → 다음 한국 거래일 테마(소속 종목 중앙값) 등락.
①은 기존 바스켓과 새 바스켓의 예측력을 같은 잣대로 비교, ②③은 read-across 기울기가
기존 검증(미국 +3%↑ → 한국 상승확률 상승)과 같은 모양으로 나오는지 확인.
"""
from __future__ import annotations

import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import boot  # noqa: F401
import tickers
from db import connect
from net import RunLog, fetch_json
from replay import load_bars

YF = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3y&interval=1d"

CASES = {
    "AI SW (기존 4종)": {"us": ["PLTR", "AI", "SNOW", "TEAM"], "kr_theme_db": "AI 소프트웨어"},
    "AI SW (확충 8종)": {"us": ["PLTR", "AI", "SNOW", "TEAM", "ORCL", "NOW", "CRM", "DDOG"],
                        "kr_theme_db": "AI 소프트웨어"},
    "사이버보안 (신규)": {"us": ["PANW", "CRWD", "ZS", "FTNT"],
                     "kr": ["안랩", "윈스", "이글루", "라온시큐어", "드림시큐리티",
                            "파이오링크", "케이사인", "시큐브", "지니언스", "모니터랩"]},
    "에너지·정유 (신규)": {"us": ["XOM", "CVX", "COP"],
                      "kr": ["S-Oil", "SK이노베이션", "GS", "흥구석유", "중앙에너비스",
                             "한국석유", "극동유화", "대성산업"]},
}


def us_series(syms: list[str], log: RunLog) -> dict[str, float]:
    """바스켓 중앙값의 일자별 등락 시계열."""
    def one(sym):
        try:
            res = fetch_json(YF.format(sym=sym), timeout=25)["chart"]["result"][0]
            ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
            s, prev = {}, None
            for t, c in zip(ts, cl):
                if c is None:
                    continue
                d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
                if prev:
                    s[d] = (c / prev - 1) * 100
                prev = c
            return sym, s
        except Exception:  # noqa: BLE001
            return sym, None

    got = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for sym, s in pool.map(one, syms):
            if s:
                got[sym] = s
            else:
                log.warn("exp", f"{sym} 수신 실패")
    dates = set().union(*(set(s) for s in got.values()))
    return {d: statistics.median([s[d] for s in got.values() if d in s])
            for d in dates if sum(1 for s in got.values() if d in s) >= max(2, len(got) // 2)}


def prior_us(series: dict[str, float], kr_date: str) -> float | None:
    for back in range(1, 6):
        d = (datetime.strptime(kr_date, "%Y%m%d").date() - timedelta(days=back)).isoformat()
        if d in series:
            return series[d]
    return None


def main():
    log = RunLog()
    bars = load_bars()

    with connect() as conn:
        db_members = {}
        for r in conn.execute("SELECT code, themes FROM stocks WHERE themes IS NOT NULL"):
            for t in (x.strip() for x in r["themes"].split(",")):
                db_members.setdefault(t, []).append(r["code"])

    idx = {c: {b["date"]: i for i, b in enumerate(bs)} for c, bs in bars.items()}
    cal = [b["date"] for b in bars["005930"]][60:]

    print("\n" + "=" * 78)
    print("바스켓 확충안 — 간밤 미국 → 다음날 한국 테마 (2년)")
    print("=" * 78)

    for name, spec in CASES.items():
        us = us_series(spec["us"], log)

        if "kr_theme_db" in spec:
            codes = db_members.get(spec["kr_theme_db"], [])
        else:
            codes, missing = [], []
            for n in spec["kr"]:
                c = tickers.to_code(n)
                (codes.append(c) if c and c in bars else missing.append(n))
            if missing:
                log.warn("exp", f"{name}: 코드 미매칭 {missing}")
        if len(codes) < 4:
            print(f"\n▸ {name}: 국내 종목 부족({len(codes)}) — 판단 불가")
            continue

        pairs = []
        for day in cal:
            u = prior_us(us, day)
            if u is None:
                continue
            vals = []
            for c in codes:
                i = idx.get(c, {}).get(day)
                if i and i > 0 and bars[c][i - 1]["close"] > 0:
                    vals.append((bars[c][i]["close"] / bars[c][i - 1]["close"] - 1) * 100)
            if len(vals) >= 4:
                pairs.append((u, statistics.median(vals)))

        if len(pairs) < 100:
            print(f"\n▸ {name}: 표본 부족({len(pairs)})")
            continue

        base_up = sum(1 for _, k in pairs if k > 0) / len(pairs) * 100
        try:
            corr = statistics.correlation([u for u, _ in pairs], [k for _, k in pairs])
        except Exception:  # noqa: BLE001
            corr = float("nan")

        hot = [k for u, k in pairs if u >= 3]
        cold = [k for u, k in pairs if u <= -3]
        print(f"\n▸ {name}  (표본 {len(pairs):,} · 국내 {len(codes)}종목 · 상관 {corr:+.3f})")
        print(f"    기준선: 상승확률 {base_up:.1f}%")
        for lab, sel in (("미국 +3%↑ 다음날", hot), ("미국 -3%↓ 다음날", cold)):
            if len(sel) < 15:
                print(f"    {lab:<16}: 표본 부족({len(sel)})")
                continue
            up = sum(1 for k in sel if k > 0) / len(sel) * 100
            print(f"    {lab:<16}: n={len(sel):>4} · 상승확률 {up:5.1f}% "
                  f"({up - base_up:+.1f}%p) · 평균 {statistics.mean(sel):+.2f}%")


if __name__ == "__main__":
    main()
