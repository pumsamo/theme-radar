"""가치 스크린 — 영업이익 증가 ∩ 부채비율 120% 이하 TOP20 (사용자 확정 2026-09-01).

근거: 8/31 소급 검증 — 2025-04 형성 단일 코호트 17개월 +38.4% (기준 +19.7%).
매출성장 단독·이익성장 단독은 무효, 이 결합만 유의미했다. 단일 코호트라 '유망' 단계 —
그래서 관찰 시트로 성적을 계속 쌓는다. 중장기(수개월~년) 관찰용, 단기 트랙과 무관.

조건: 최신 확정 연간 영업이익 증가율 상위 (직전·최신 연도 모두 흑자) AND 부채비율 ≤120%
     AND 거래대금 20일 평균 10억+ (유동성 하한). TOP20.
실행: 매주 금요일 주간 세트 (재무는 연 4회 공시라 주간 갱신이면 과분).
저장: data/value_screen.json → 현황판 '가치' 시트가 읽는다.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import replay
import tickers
from net import fetch

OUT = Path(__file__).resolve().parent.parent / "data" / "value_screen.json"
FIN_URL = "https://m.stock.naver.com/api/stock/{code}/finance/annual"


def fin(code):
    try:
        d = json.loads(fetch(FIN_URL.format(code=code), timeout=15).decode())
        fi = d["financeInfo"]
        firm = {t["key"] for t in fi["trTitleList"] if t.get("isConsensus") != "Y"}
        out = {}
        for row in fi["rowList"]:
            if row["title"] in ("영업이익", "부채비율"):
                out[row["title"]] = {int(k[:4]): float(v["value"].replace(",", ""))
                                     for k, v in row["columns"].items()
                                     if k in firm and v and v.get("value") not in (None, "", "-")}
        return code, out
    except Exception:  # noqa: BLE001
        return code, None


def main() -> None:
    bars_all = replay.load_bars()
    codes = sorted(bars_all.keys())
    code_name = {}
    try:
        for nm, info in tickers.table().items():
            c = info.get("code") if isinstance(info, dict) else None
            if c:
                code_name[c] = nm
    except Exception:  # noqa: BLE001
        pass

    with ThreadPoolExecutor(8) as ex:
        fins = dict(ex.map(fin, codes))

    rows = []
    for code, f in fins.items():
        if not f:
            continue
        op = f.get("영업이익", {})
        ys = sorted(y for y in op if y <= _date.today().year)
        if len(ys) < 2:
            continue
        y1, y0 = ys[-1], ys[-2]
        if op[y0] <= 0 or op[y1] <= 0:
            continue  # 두 해 모두 흑자
        debt = f.get("부채비율", {}).get(y1)
        if debt is None or debt > 120:
            continue
        bars = bars_all.get(code, [])
        if len(bars) < 20:
            continue
        v20 = sum(b["close"] * b["volume"] for b in bars[-20:]) / 20
        if v20 < 10e8:
            continue
        rows.append({"code": code, "name": code_name.get(code, code),
                     "opg": (op[y1] / op[y0] - 1) * 100, "op": op[y1],
                     "fy": y1, "debt": debt, "val": v20 / 1e8})
    rows.sort(key=lambda r: -r["opg"])
    top = rows[:20]
    OUT.write_text(json.dumps({"asof": _date.today().isoformat(),
                               "universe": len(rows), "rows": top},
                              ensure_ascii=False), encoding="utf-8")
    print(f"가치 스크린: 통과 {len(rows)}종목 중 TOP20 저장 (기준 FY{top[0]['fy'] if top else '?'})")
    for r in top[:5]:
        print(f"  {r['name']}: 영업이익 +{r['opg']:,.0f}% ({r['op']:,.0f}억) · 부채 {r['debt']:.0f}%")


if __name__ == "__main__":
    main()
