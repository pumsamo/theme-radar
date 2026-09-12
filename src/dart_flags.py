"""보유·관심 종목 최근 공시 플래그 — 저녁 루틴용 (2026-09-12 신설).

data/dart/market_{B,I}_{이번달}.json(collect_dart.py가 이번 달을 매번 다시 받음)에서 config/holdings.json의 보유·관심 종목 공시를
최근 N일치 뽑아 dart_events.TYPES로 분류해 출력한다. 회피 유형(유상증자·CB 발행·감자·관리종목·불성실공시·조회공시·최대주주 변경)은 ⚠ 표시.
실행: python src/collect_dart.py --from <이번달> && python src/dart_flags.py [일수=7]
매매 지시 아님 — 검증 표(dart_events)의 승률을 붙여 참고만.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date as _date, timedelta
from pathlib import Path

import boot  # noqa: F401
import dart_events as de

ROOT = Path(__file__).resolve().parent.parent
AVOID = {"유상증자 결정", "CB·BW·EB 발행 결정", "감자 결정", "관리종목 지정·실질심사", "불성실공시 지정·예고",
         "조회공시 요구(시황변동)", "최대주주 변경", "자기CB 재매각 결정", "영업정지", "소송 제기"}


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 7
    since = (_date.today() - timedelta(days=days)).strftime("%Y%m%d")
    h = json.loads((ROOT / "config" / "holdings.json").read_text(encoding="utf-8"))
    names = {x["code"]: (x["name"], kind) for kind, key in (("보유", "holdings"), ("관심", "watchlist")) for x in h.get(key, [])}
    stats = {}
    ev = ROOT / "data" / "dart_events.json"
    if ev.exists():
        for r in json.loads(ev.read_text(encoding="utf-8")).get("summary", []):
            stats[r["type"]] = r
    rows = []
    for p in sorted((ROOT / "data" / "dart").glob("market_*.json")):
        if p.name.split("_")[2][:6] < since[:6]:
            continue
        for r in json.loads(p.read_text(encoding="utf-8")):
            if r.get("stock_code") in names and r.get("rcept_dt", "") >= since:
                rows.append(r)
    if not rows:
        print(f"보유·관심 종목 최근 {days}일 공시 없음 (수집 기준 {since}~)")
        return
    print(f"보유·관심 종목 최근 {days}일 공시 {len(rows)}건:")
    for r in sorted(rows, key=lambda x: x["rcept_dt"], reverse=True):
        nm, kind = names[r["stock_code"]]
        title = re.sub(r"\s+", " ", r["report_nm"]).strip()
        typ = de.classify(title)
        st = stats.get(typ)
        tail = (f" — 검증 D+20 승률 {st['d20_win']:.0%}·중앙 {st['d20_med']:+.1f}% (n={st['n']})" if st and "d20_win" in st else "")
        flag = "⚠ " if typ in AVOID else ("✔ " if typ in ("자사주 취득 결정", "무상증자 결정") else "  ")
        print(f"  {flag}{r['rcept_dt'][4:6]}/{r['rcept_dt'][6:]} {nm}({kind}) {title[:50]}" + (f" [{typ}]" if typ else "") + tail)


if __name__ == "__main__":
    main()
