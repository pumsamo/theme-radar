"""448선 대형주 가설 2부 — 실적 필터 소급 검증 (사용자 가설, 2026-08-28 실행).

bt_448의 초대형 슬라이스(거래대금 상위 1/4)에 "신호 시점에 이미 공시돼 있던
연간 영업이익"을 소급 적용해 실적 그룹별 R을 비교한다.

미래 훔쳐보기 방지 (사전 고정):
  사업연도 Y의 연간 실적은 Y+1년 4월 1일부터 사용 가능으로 간주
  (사업보고서 법정 기한 90일의 보수적 적용. 공시일 단위 정밀도는 없지만
  '더 늦게 아는 쪽'으로 치우친 근사라 결과를 부풀리지 않는다).

분할 (사전 등록):
  A. 전체: 최신 가용 연간 영업이익 흑자 vs 적자
  B. 2025-04-01 이후 신호만: 영업이익 YoY 증가 vs 감소 (데이터가 2023년부터라
     그 이전 신호는 YoY 계산 불가 — 표본에서 제외하고 개수 보고)

판정선 (어제 사전 등록): 증가 그룹 평균 ≥ +0.117R(A급)이면 그림자 트랙 신설 검토,
+0.058R 부근 정체면 실적 필터 기각.

데이터: 시세 = bt_448과 동일(네이버 4년). 실적 = m.stock.naver.com 연간 재무 API
(단위: 억 원, 연결 기준). 수수료 미반영.
"""
from __future__ import annotations

import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

import boot  # noqa: F401
from db import connect
from net import fetch
from prices_kr import fetch_ohlc

START, END = "20220801", "20260827"
NEAR, HOLD, COOL = 0.03, 20, 20
FIN_URL = "https://m.stock.naver.com/api/stock/{code}/finance/annual"


def fetch_bars(code):
    try:
        return code, fetch_ohlc(code, START, END)
    except Exception:  # noqa: BLE001
        return code, []


def fetch_op(code):
    """code → {사업연도: 영업이익(억)} — 확정치만 (컨센서스 컬럼 제외)."""
    try:
        d = json.loads(fetch(FIN_URL.format(code=code), timeout=15).decode())
        fi = d["financeInfo"]
        firm = {t["key"] for t in fi["trTitleList"] if t.get("isConsensus") != "Y"}
        for row in fi["rowList"]:
            if row["title"] == "영업이익":
                out = {}
                for k, v in row["columns"].items():
                    if k not in firm or not v or v.get("value") in (None, "", "-"):
                        continue
                    try:
                        out[int(k[:4])] = float(v["value"].replace(",", ""))
                    except ValueError:
                        continue
                return code, out
        return code, {}
    except Exception:  # noqa: BLE001
        return code, {}


def simulate(bars, i, ma):
    if i + 1 >= len(bars):
        return None
    entry = bars[i + 1]["open"]
    stop = ma * 0.97
    if entry <= stop:
        return None
    risk = entry - stop
    target = entry + 2 * risk
    for b in bars[i + 1:i + 1 + HOLD]:
        if b["low"] <= stop:
            return -1.0
        if b["high"] >= target:
            return 2.0
    return (bars[min(i + HOLD, len(bars) - 1)]["close"] - entry) / risk


def latest_fy(sig_date: str) -> int:
    """신호일에 사용 가능한 최신 사업연도 (Y는 Y+1-04-01부터 가용)."""
    y, md = int(sig_date[:4]), sig_date[4:]
    return y - 1 if md >= "0401" else y - 2


def main():
    db = connect()
    codes = {c: n for c, n in db.execute("select code, name from stocks where themes != ''")}
    with ThreadPoolExecutor(8) as ex:
        data = dict(ex.map(fetch_bars, codes))

    trades = []
    for code, bars in data.items():
        if len(bars) < 470:
            continue
        closes = [b["close"] for b in bars]
        last_sig = -999
        for i in range(448, len(bars) - 1):
            ma = sum(closes[i - 447:i + 1]) / 448
            if abs(closes[i] / ma - 1) > NEAR or i - last_sig < COOL:
                continue
            if not any(closes[j] > ma * 1.03 for j in range(max(448, i - 60), i)):
                continue
            r = simulate(bars, i, ma)
            if r is None:
                continue
            v20 = sum(b["close"] * b["volume"] for b in bars[i - 19:i + 1]) / 20
            trades.append({"code": code, "name": codes[code],
                           "date": bars[i]["date"], "r": r, "val": v20})
            last_sig = i

    top_q = sorted(t["val"] for t in trades)[int(len(trades) * 0.75)]
    big = [t for t in trades if t["val"] >= top_q]
    print(f"전체 {len(trades)}건 · 초대형(상위 1/4, {top_q/1e8:.0f}억+) {len(big)}건 · "
          f"대상 종목 {len({t['code'] for t in big})}개")

    with ThreadPoolExecutor(8) as ex:
        ops = dict(ex.map(fetch_op, {t["code"] for t in big}))
    print(f"실적 확보 {sum(1 for v in ops.values() if v)}종목")

    def agg(rows, label):
        rs = [t["r"] for t in rows]
        if len(rs) < 5:
            print(f"  {label}: 표본 {len(rs)}건 — 부족")
            return
        win = sum(1 for r in rs if r > 0) / len(rs)
        print(f"  {label}: n={len(rs)} · 평균 {statistics.mean(rs):+.3f}R · "
              f"중앙값 {statistics.median(rs):+.2f}R · 승률 {win:.1%}")

    # A. 흑자 vs 적자 (최신 가용 연도 기준)
    black, red, nodata = [], [], 0
    for t in big:
        fy = latest_fy(t["date"])
        op = ops.get(t["code"], {}).get(fy)
        if op is None:
            nodata += 1
        elif op > 0:
            black.append(t)
        else:
            red.append(t)
    print(f"\nA. 최신 가용 연간 영업이익 (실적 미확보 신호 {nodata}건 제외)")
    agg(black, "흑자")
    agg(red, "적자")

    # B. YoY 증감 (2025-04-01 이후 신호만 — 전년도 데이터 가용 구간)
    up, down, skip = [], [], 0
    for t in big:
        if t["date"] < "20250401":
            continue
        fy = latest_fy(t["date"])
        cur, prev = ops.get(t["code"], {}).get(fy), ops.get(t["code"], {}).get(fy - 1)
        if cur is None or prev is None:
            skip += 1
        elif cur > prev:
            up.append(t)
        else:
            down.append(t)
    print(f"\nB. 영업이익 YoY (2025-04 이후 신호 한정, 데이터 부족 {skip}건 제외)")
    agg(up, "증가")
    agg(down, "감소")
    up_black = [t for t in up if ops[t["code"]][latest_fy(t["date"])] > 0]
    agg(up_black, "증가+흑자 (사용자 가설 본체)")

    print("\n비교 기준: 초대형 무필터 +0.058R · A급 +0.117R · 수수료 미반영")
    print("가용성 근사(4/1 규칙)라 분기 단위 서프라이즈는 못 잡음 — 보수적 추정.")


if __name__ == "__main__":
    main()
