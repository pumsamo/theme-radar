"""시장 국면별 성적 — "시장 전체가 나쁠 땐 안 한다"에 근거가 있는지 잰다.

백테스트 매매(픽 규칙 그대로)를 신호일의 코스피 상태로 갈라본다.
검증 계약 중이므로 픽 규칙에는 손대지 않는다 — 결과는 브리핑의 '정보 줄'로만 쓴다.

국면 정의 (전부 종가 기준, 장전에 알 수 있는 값):
  A. 코스피 20일선 위/아래
  B. 코스피 최근 5일 수익률 구간
  C. 코스피 60일 고점 대비 낙폭
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

import boot  # noqa: F401
import backtest
from net import RunLog, fetch_json

KOSPI = "https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11?range=3y&interval=1d"


def kospi_series(log: RunLog) -> dict[str, dict]:
    data = fetch_json(KOSPI, timeout=25)
    res = data["chart"]["result"][0]
    ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
    rows = [(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y%m%d"), c)
            for t, c in zip(ts, closes) if c]
    out: dict[str, dict] = {}
    cl = [c for _, c in rows]
    for i, (d, c) in enumerate(rows):
        if i < 60:
            continue
        ma20 = statistics.mean(cl[i - 19:i + 1])
        out[d] = {
            "above_ma20": c > ma20,
            "ret5": (c / cl[i - 5] - 1) * 100,
            "draw60": (c / max(cl[i - 59:i + 1]) - 1) * 100,
        }
    log.ok("regime", f"코스피 {len(out)}일")
    return out


def show(sel, label, base):
    filled = [t["r"] for t in sel if t.get("entered") and t.get("r") is not None]
    if len(filled) < 200:
        print(f"  {label:<28} 표본 부족({len(filled)})")
        return
    win = sum(1 for r in filled if r > 0) / len(filled) * 100
    avg = statistics.mean(filled)
    print(f"  {label:<28} n={len(filled):>6,}  승 {win:5.1f}%  평균 {avg:+.3f}R  "
          f"(기준대비 {avg - base:+.3f})")


def main():
    log = RunLog()
    kospi = kospi_series(log)
    trades = backtest.run("all", None, "20240711", "20260810", log)

    tagged = [(t, kospi.get(t["date"])) for t in trades]
    tagged = [(t, k) for t, k in tagged if k]
    allr = [t["r"] for t, _ in tagged if t.get("entered") and t.get("r") is not None]
    base = statistics.mean(allr)

    print("\n" + "=" * 74)
    print(f"시장 국면별 성적 — 픽 규칙 동일, 신호일의 코스피 상태로만 구분 "
          f"(체결 {len(allr):,}건, 전체 {base:+.3f}R)")
    print("=" * 74)

    print("\n  ▸ 코스피 20일선")
    show([t for t, k in tagged if k["above_ma20"]], "위 (상승 국면)", base)
    show([t for t, k in tagged if not k["above_ma20"]], "아래 (하락 국면)", base)

    print("\n  ▸ 코스피 최근 5일")
    for lo, hi, lab in ((-99, -3, "-3% 이하 (급락중)"), (-3, 0, "-3~0%"),
                        (0, 3, "0~+3%"), (3, 99, "+3% 이상 (급등중)")):
        show([t for t, k in tagged if lo <= k["ret5"] < hi], lab, base)

    print("\n  ▸ 코스피 60일 고점 대비")
    for lo, hi, lab in ((-99, -10, "-10% 이하 (조정 깊음)"), (-10, -5, "-10~-5%"),
                        (-5, -2, "-5~-2%"), (-2, 1, "-2% 이내 (고점권)")):
        show([t for t, k in tagged if lo <= k["draw60"] < hi], lab, base)


if __name__ == "__main__":
    main()
