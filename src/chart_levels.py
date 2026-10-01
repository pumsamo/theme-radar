"""차트 보조 지표 — 이동평균·볼린저밴드·지지/저항·패턴 태그 (2026-10-01 사용자 요청: "이평선, 볼린저밴드, 저항과 지지 패턴").

표시 전용이다. 픽·라벨 규칙(검증 계약, ~2026-11-04 동결)에는 쓰지 않는다. 저녁 루틴에서 보유·관심 종목 점검에
한 줄씩 붙여 보여주고, 규칙 반영 여부는 11월 연구에서 백테스트로 정한다.

- 이동평균: 5·20·60·120·224일 (일봉 종가 — 2026-09-14부터 일봉에 KRX 애프터마켓 체결 포함)
- 볼린저밴드: 20일 평균 ± 2σ, %B(밴드 안 위치 0~1), 밴드폭의 최근 120일 순위(하위 20% 이하 = 수축)
- 지지/저항: 최근 250일 스윙 고점·저점(앞뒤 5일 중 최고·최저)을 ±2% 안에서 묶은 가격대와 닿은 횟수
- 패턴: 정배열·역배열·박스권·밴드 수축·밴드 상단 돌파/하단 이탈·60일 고점권·상승 중 20일선 눌림

실행: python src/chart_levels.py --holdings        (저녁 루틴, holdings_alert 다음 — 보유★·관심☆ 한 줄씩)
      python src/chart_levels.py 006660 한중엔시에스  (종목 상세)
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import boot  # noqa: F401
import tickers
from prices_kr import fetch_ohlc

ROOT = Path(__file__).resolve().parent.parent
TICK = [(2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500), (10**12, 1000)]


def tick_round(p: float) -> int:
    for lim, t in TICK:
        if p < lim:
            return int(round(p / t) * t)
    return int(p)


def swing_levels(bars, lookback=250, win=5, tol=0.02):
    """스윙 고점·저점을 모아 ±tol 안에서 군집 → [(평균 가격, 닿은 횟수, 마지막 날짜)]."""
    b = bars[-lookback:]
    pts = []
    for i in range(win, len(b) - win):
        if b[i]["high"] == max(x["high"] for x in b[i - win:i + win + 1]):
            pts.append((b[i]["high"], b[i]["date"]))
        if b[i]["low"] == min(x["low"] for x in b[i - win:i + win + 1]):
            pts.append((b[i]["low"], b[i]["date"]))
    pts.sort()
    groups: list[dict] = []
    for p, d in pts:
        if groups and p <= groups[-1]["lo"] * (1 + tol):
            groups[-1]["ps"].append(p)
            groups[-1]["ds"].append(d)
        else:
            groups.append({"lo": p, "ps": [p], "ds": [d]})
    return [(statistics.mean(g["ps"]), len(g["ps"]), max(g["ds"])) for g in groups]


def pick_levels(near_first: list) -> list:
    """가까운 2개 + (그 밖에 있으면) ±25% 안에서 가장 많이 닿은 가격대(3회 이상) 1개."""
    out = near_first[:2]
    strong = max(near_first, key=lambda x: x[1], default=None)
    if strong and strong[1] >= 3 and strong not in out:
        out.append(strong)
    return out


def compute(code: str) -> dict | None:
    bars = fetch_ohlc(code, "20250601", "20991231")
    if len(bars) < 25:
        return None
    cl = [x["close"] for x in bars]
    c = cl[-1]
    mas = {n: sum(cl[-n:]) / n for n in (5, 20, 60, 120, 224) if len(cl) >= n}
    w = cl[-20:]
    mid = sum(w) / 20
    sd = statistics.pstdev(w)
    up, lo = mid + 2 * sd, mid - 2 * sd
    pb = (c - lo) / (up - lo) if up > lo else None
    bw = (up - lo) / mid
    hist = [4 * statistics.pstdev(cl[k - 19:k + 1]) / (sum(cl[k - 19:k + 1]) / 20) for k in range(max(19, len(cl) - 120), len(cl))]
    bw_rank = sum(1 for x in hist if x <= bw) / len(hist) * 100
    lv = swing_levels(bars)
    res = pick_levels(sorted([x for x in lv if c * 1.005 < x[0] <= c * 1.25], key=lambda x: x[0]))
    sup = pick_levels(sorted([x for x in lv if c * 0.75 <= x[0] < c * 0.995], key=lambda x: -x[0]))
    hi60 = max(x["high"] for x in bars[-60:])
    tags = []
    seq = [mas.get(n) for n in (5, 20, 60, 120)]
    if all(seq) and seq == sorted(seq, reverse=True):
        tags.append("정배열")
    elif all(seq) and seq == sorted(seq):
        tags.append("역배열")
    rng40 = max(x["high"] for x in bars[-40:]) / min(x["low"] for x in bars[-40:]) - 1
    if rng40 < 0.15:
        tags.append(f"박스권(40일 폭 {rng40 * 100:.0f}%)")
    if bw_rank <= 20:
        tags.append("밴드 수축")
    if pb is not None and pb > 1:
        tags.append("밴드 상단 돌파")
    elif pb is not None and pb < 0:
        tags.append("밴드 하단 이탈")
    if c >= hi60 * 0.99:
        tags.append("60일 고점권")
    if 20 in mas and 60 in mas and abs(c / mas[20] - 1) < 0.02 and mas[20] > mas[60]:
        tags.append("상승 중 20일선 눌림")
    return {"close": c, "date": bars[-1]["date"], "mas": mas, "up": up, "mid": mid, "lo": lo, "pb": pb,
            "bw": bw, "bw_rank": bw_rank, "res": res, "sup": sup, "tags": tags}


def fmt_lv(levels, c):
    return " ".join(f"{tick_round(p):,}({(p / c - 1) * 100:+.1f}%·{n}회)" for p, n, _ in levels) or "없음"


def one_line(mark: str, name: str, code: str) -> str:
    r = compute(code)
    if not r:
        return f"{mark}{name} — 데이터 부족"
    c = r["close"]
    above = [str(n) for n, v in r["mas"].items() if c >= v]
    below = [str(n) for n, v in r["mas"].items() if c < v]
    ma_txt = (f"위 {'·'.join(above)}" if above else "") + (" / " if above and below else "") + (f"아래 {'·'.join(below)}" if below else "")
    if 224 in r["mas"]:
        ma_txt += f" (224선 {(c / r['mas'][224] - 1) * 100:+.1f}%)"
    pb = f"{r['pb']:.2f}" if r["pb"] is not None else "-"
    return (f"{mark}{name} {c:,.0f} | 이평 {ma_txt} | 볼린저 %B {pb}·폭 하위 {r['bw_rank']:.0f}% | "
            f"저항 {fmt_lv(r['res'], c)} | 지지 {fmt_lv(r['sup'], c)}" + (f" | {' · '.join(r['tags'])}" if r["tags"] else ""))


def detail(code: str) -> None:
    r = compute(code)
    name = tickers.to_name(code) or code
    if not r:
        print(f"{name}({code}) — 데이터 부족")
        return
    c = r["close"]
    print(f"===== {name}({code}) 종가 {c:,.0f} ({r['date']})")
    print("  이평선: " + " · ".join(f"{n}일 {v:,.0f}({(c / v - 1) * 100:+.1f}%)" for n, v in r["mas"].items()))
    pb = f"{r['pb']:.2f}" if r["pb"] is not None else "-"
    print(f"  볼린저(20,2σ): 상단 {tick_round(r['up']):,} · 중심 {tick_round(r['mid']):,} · 하단 {tick_round(r['lo']):,} · %B {pb} · "
          f"밴드폭 {r['bw'] * 100:.1f}%(최근 120일 중 하위 {r['bw_rank']:.0f}%)")
    print(f"  저항(위): {fmt_lv(r['res'], c)}")
    print(f"  지지(아래): {fmt_lv(r['sup'], c)}")
    print("  패턴: " + (" · ".join(r["tags"]) or "특이 없음"))


def main() -> None:
    args = sys.argv[1:]
    if "--holdings" in args:
        conf = json.loads((ROOT / "config" / "holdings.json").read_text(encoding="utf-8"))
        print("★ 차트 보조 (표시 전용 · 규칙 아님) — 보유★ · 관심☆")
        for mark, key in (("★", "holdings"), ("☆", "watchlist")):
            for h in conf.get(key, []):
                print("  " + one_line(mark, h["name"], h["code"]))
        return
    for a in args:
        code = a if a.isdigit() or (len(a) == 6 and a[:4].isdigit()) else tickers.to_code(a)
        if not code:
            print(f"{a}: 종목코드를 찾지 못함 — 추측하지 않음")
            continue
        detail(code)


if __name__ == "__main__":
    main()
