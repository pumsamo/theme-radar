"""가상 계좌 원장 — "종자돈 1,000만원이었으면 지금 얼마인가" (사용자 제안 2026-08-28).

계약 채점(R 단위)의 회계 버전이다. 규칙 변경이 아니라 표시 방식 추가일 뿐 —
픽 생성·채점 로직은 그대로 두고, 같은 거래를 돈으로 환산해 잔고를 추적한다.

고정 규칙 (2026-08-28 확정, 11월 판정까지 불변):
  시작: 2026-08-11 · 종자돈 10,000,000원
  리스크: 1건당 100,000원 고정 (시작자금의 1%, 복리 없음 — 단순·투명 우선)
  포지션 주수 = 리스크 / (진입가 − 손절가), 매수금액이 가용현금 초과 시 현금만큼 축소
  진입: 픽 기록 후 3거래일 내 진입가(전일 고가 돌파) 터치 시 그 가격, 미돌파 소멸
  청산: 손절 우선 → 목표(진입+2×리스크폭) → 20거래일 종가 청산
  비용: 왕복 0.3% (수수료+거래세+슬리피지 근사) — 매도 시 일괄 차감
  대상: tier='pick' (아침 계약 픽). 저녁 스캔 트랙은 동시 수십 종목이라
        현금 제약상 실전 재현이 안 돼 제외 (R 지표로만 본다).

그림자 청산 병기 (2026-09-08부터, 표시 전용 · 계약 판정 미반영 · 사용자 지시 9/7):
  같은 체결(진입·손절·주수)에 청산만 바꾼 결과를 나란히 계산한다.
    ×5 = 목표 없이 '거래량 ≥ 20일 평균 × 5 인 첫날 종가 청산' · 손절 우선 · 20일 기한 (bt_volexit V1, +0.189R vs 기준 +0.128R)
    ×2 = 같은 규칙에 k=2 (회전형, 승률 51% · 보유 6.9일)
  시총 구간(체결가 × 현재 상장주식수, data/mcap.json 근사)도 트레이드마다 붙여 R트랙을 구간별로 본다 (bt_mcap 전방 확인).

실행: python src/ledger.py  (저녁 루틴 ⑥단계)
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
from db import connect
from prices_kr import fetch_ohlc

START_DATE = "2026-08-11"
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000   # python ledger.py 30000000
RISK = SEED // 100                                             # 1% 리스크 연동
COST = 0.003
ENTRY_WINDOW = 3
HOLD = 20
# 진입 탐색 시작일 = 픽 날짜 D 당일 (계약: 진입가 = 전일 고가×1.002, D·D+1·D+2 3거래일 창 — score_day ③·백테스트와 동일).
# 2026-09-08 버그 수정: 종전 코드는 D+1부터 탐색해 하루 늦게 체결됐다(저녁 스캔 트랙의 '다음 날부터' 규칙이 잘못 복사됨).
ENTRY_SAME_DAY = True
ROOT = Path(__file__).resolve().parent.parent
FETCH_START = "20260615"  # 그림자 청산의 20일 평균 거래량용 여유분 — 계약 진입 탐색은 픽 다음 날부터라 영향 없음
SHADOW_K = (5, 2)         # 그림자 청산 배수 (표시 전용)
BUCKETS = [(0, 500, "<500억"), (500, 1000, "500~1,000억"), (1000, 5000, "1,000~5,000억"),
           (5000, 20000, "5,000억~2조"), (20000, 1e12, "2조 이상")]  # bt_mcap.BUCKETS와 동일
BUCKET_ORDER = [lab for _, _, lab in BUCKETS] + ["미상"]


def bucket(cap_eok) -> str:
    if cap_eok is None:
        return "미상"
    for lo, hi, lab in BUCKETS:
        if lo <= cap_eok < hi:
            return lab
    return "미상"


def load_shares() -> dict[str, int]:
    """data/mcap.json(collect_mcap.py 스냅샷)의 상장주식수. 없으면 빈 dict → 구간 '미상'."""
    try:
        mc = json.loads((ROOT / "data" / "mcap.json").read_text(encoding="utf-8"))
        return {c: int(v["shares"]) for c, v in mc["stocks"].items() if v.get("shares")}
    except Exception:  # noqa: BLE001
        return {}


def shadow_exit(bars: list[dict], i0: int, stop: float, k: float):
    """그림자 청산 탐색: 손절 우선 → 거래량 ≥ 20일 평균×k 인 첫날 종가 → 20일 기한 종가.
    bt_volexit.simulate(mode='v1')와 동일 (체결일 포함, 손절이 같은 날이면 손절). 미종결이면 (None, None, None)."""
    vol = [b["volume"] for b in bars]
    for t, b in enumerate(bars[i0:i0 + HOLD]):
        idx = i0 + t
        if b["low"] <= stop:
            return stop, b["date"], "손절"
        prev = vol[max(0, idx - 20):idx]
        v20 = sum(prev) / len(prev) if prev else 0
        if v20 > 0 and vol[idx] >= k * v20:
            return b["close"], b["date"], f"거래량×{vol[idx] / v20:.1f}"
    if len(bars) - 1 >= i0 + HOLD - 1:
        last = bars[i0 + HOLD - 1]
        return last["close"], last["date"], "기한 청산"
    return None, None, None


def compute(seed: int = SEED) -> dict:
    global RISK
    RISK = seed // 100
    db = connect()
    rows = db.execute(
        """select date, code, name, entry, stop from candidates
           where tier='pick' and date >= ? and entry is not null and stop is not null
           order by date""", (START_DATE,)).fetchall()
    # 같은 날 같은 종목이 news·readacross 양쪽에서 픽되면 한 번만 산다
    seen, picks = set(), []
    for r in rows:
        if (r[0], r[1]) in seen:
            continue
        seen.add((r[0], r[1]))
        picks.append(r)
    codes = sorted({p[1] for p in picks})

    def get(code):
        try:
            return code, fetch_ohlc(code, FETCH_START, _date.today().strftime("%Y%m%d"))
        except Exception:  # noqa: BLE001
            return code, []
    with ThreadPoolExecutor(8) as ex:
        bars_all = dict(ex.map(get, codes))
    shares_out = load_shares()

    cash = seed
    open_pos = []   # {name, code, shares, fill, stop, target, opened, deadline_idx}
    closed = []     # {name, date, pnl, label}
    skipped = []

    events = []  # (판정일 정렬용) — 픽마다 진입 시도부터 청산까지 시뮬
    for pdate, code, name, entry, stop in picks:
        bars = bars_all.get(code, [])
        d0 = pdate.replace("-", "")
        idx = next((i for i, b in enumerate(bars)
                    if (b["date"] >= d0 if ENTRY_SAME_DAY else b["date"] > d0)), None)
        if idx is None:
            skipped.append((name, pdate, "시세 없음"))
            continue
        fill_i, fill_px = None, None
        for i in range(idx, min(idx + ENTRY_WINDOW, len(bars))):
            if bars[i]["high"] >= entry:
                fill_i = i
                fill_px = max(entry, bars[i]["open"])  # 갭 상방 시 시가 체결
                break
        if fill_i is None:
            closed.append({"name": name, "date": pdate, "pnl": 0.0, "label": "미체결 소멸"})
            continue
        risk_per_share = fill_px - stop
        if risk_per_share <= 0:
            skipped.append((name, pdate, "손절가 이상 체결"))
            continue
        shares = int(RISK / risk_per_share)
        if shares <= 0:
            skipped.append((name, pdate, f"고가주 — 1주 리스크 {risk_per_share:,.0f}원 > 10만원"))
            continue
        cap = fill_px * shares_out[code] / 1e8 if code in shares_out else None
        events.append({"name": name, "code": code, "pdate": pdate, "bars": bars,
                       "fill_i": fill_i, "fill": fill_px, "stop": stop,
                       "target": fill_px + 2 * risk_per_share, "shares": shares,
                       "cap_eok": cap, "bucket": bucket(cap)})

    # 시간순 현금 관리: 체결일 기준 정렬해 현금 한도 적용
    events.sort(key=lambda e: e["bars"][e["fill_i"]]["date"])
    holding: dict[str, str] = {}  # code → 청산일 (그 전엔 같은 종목 재진입 금지)
    txns = []       # (일자, 현금흐름) — 잔고 시계열 재구성용
    intervals = []  # {closes, shares, start, end} — 보유 구간 평가용
    for e in events:
        fill_dt = e["bars"][e["fill_i"]]["date"]
        if e["code"] in holding and fill_dt <= holding[e["code"]]:
            skipped.append((e["name"], e["pdate"], "동일 종목 보유 중 — 재진입 스킵"))
            continue
        notional = e["fill"] * e["shares"]
        cap = seed * 0.2  # 한 종목 최대 20% (집중 위험 상한)
        if notional > cap:
            e["shares"] = int(cap / e["fill"])
            notional = e["fill"] * e["shares"]
        if notional > cash:
            e["shares"] = int(cash / e["fill"])
            if e["shares"] <= 0:
                skipped.append((e["name"], e["pdate"], "현금 부족"))
                continue
            notional = e["fill"] * e["shares"]
        cash -= notional
        bars, i0 = e["bars"], e["fill_i"]
        # 그림자 청산 (표시 전용): 같은 체결에 청산만 다르게 — R은 계약 채점과 같은 방식(손절 −1, 그 외 손익/리스크)
        shadow = {}
        for k in SHADOW_K:
            px, dt, lab = shadow_exit(bars, i0, e["stop"], k)
            if px is None:
                pl = (bars[-1]["close"] - e["fill"]) * e["shares"]
                shadow[k] = {"label": "진행", "date": None, "pnl": pl, "r": pl / RISK}
            else:
                pr = px * e["shares"]
                pl = pr - notional - (notional + pr) * COST / 2
                shadow[k] = {"label": lab, "date": dt, "pnl": pl, "r": -1.0 if lab == "손절" else pl / RISK}
        meta = {"shadow": shadow, "cap_eok": e["cap_eok"], "bucket": e["bucket"], "fill_date": fill_dt}
        # 청산 탐색
        exit_px, exit_dt, label = None, None, None
        for b in bars[i0:i0 + HOLD]:
            if b["low"] <= e["stop"]:
                exit_px, exit_dt, label = e["stop"], b["date"], "손절"
                break
            if b["high"] >= e["target"]:
                exit_px, exit_dt, label = e["target"], b["date"], "목표"
                break
        if exit_px is None:
            last = bars[min(i0 + HOLD - 1, len(bars) - 1)]
            if len(bars) - 1 >= i0 + HOLD - 1:
                exit_px, exit_dt, label = last["close"], last["date"], "기한 청산"
        if exit_px is not None:
            proceeds = exit_px * e["shares"]
            fee = (notional + proceeds) * COST / 2
            cash += proceeds - fee
            pnl = proceeds - notional - fee
            closed.append({"name": e["name"], "date": exit_dt, "pnl": pnl, "label": label,
                           "r": 2.0 if label == "목표" else (-1.0 if label == "손절" else pnl / RISK), **meta})
            holding[e["code"]] = exit_dt
            txns.append((fill_dt, -notional))
            txns.append((exit_dt, proceeds - fee))
            intervals.append({"closes": {b["date"]: b["close"] for b in bars},
                              "shares": e["shares"], "start": fill_dt, "end": exit_dt})
        else:
            e["notional"] = notional
            e["meta"] = meta
            open_pos.append(e)
            holding[e["code"]] = "99999999"
            txns.append((fill_dt, -notional))
            intervals.append({"closes": {b["date"]: b["close"] for b in e["bars"]},
                              "shares": e["shares"], "start": fill_dt, "end": None})

    # 평가
    unreal = 0.0
    pos_lines = []
    for e in open_pos:
        cur = e["bars"][-1]["close"]
        pl = (cur - e["fill"]) * e["shares"]
        unreal += pl
        pos_lines.append(f"    {e['name']}: {e['shares']}주 @ {e['fill']:,.0f} → {cur:,.0f} ({pl:+,.0f}원)")
    realized = sum(c["pnl"] for c in closed)
    equity = cash + sum(e["fill"] * e["shares"] + (e["bars"][-1]["close"] - e["fill"]) * e["shares"]
                        for e in open_pos)

    # 일자별 평가금액 재구성 (파생 데이터 — 거래 판정엔 영향 없음)
    start_d = START_DATE.replace("-", "")
    days = sorted({d for iv in intervals for d in iv["closes"] if d >= start_d})
    series = []
    for d in days:
        cash_d = seed + sum(amt for dt, amt in txns if dt <= d)
        pos_d = 0.0
        for iv in intervals:
            if iv["start"] <= d and (iv["end"] is None or d < iv["end"]):
                px = iv["closes"].get(d)
                if px is None:  # 그날 휴장·데이터 결측이면 직전 종가
                    prior = [v for k, v in sorted(iv["closes"].items()) if k <= d]
                    px = prior[-1] if prior else 0
                pos_d += px * iv["shares"]
        series.append((d, cash_d + pos_d))

    return {"seed": seed, "risk": RISK, "cash": cash, "equity": equity,
            "equity_series": series,
            "realized": realized, "unreal": unreal, "closed": closed,
            "open": [{"name": e["name"], "shares": e["shares"], "fill": e["fill"],
                      "cur": e["bars"][-1]["close"],
                      "pnl": (e["bars"][-1]["close"] - e["fill"]) * e["shares"],
                      "r": (e["bars"][-1]["close"] - e["fill"]) * e["shares"] / RISK, **e["meta"]}
                     for e in open_pos],
            "skipped": skipped}


def summary(r: dict) -> dict:
    """계약 vs 그림자 청산 합계 R + 시총 구간표 (표시 전용). trades = 종결 + 보유(미체결 소멸 제외)."""
    trades = [c for c in r["closed"] if c["label"] != "미체결 소멸"] + r["open"]
    modes = {}
    for name, rf, done in (
        ("계약 +2R/20일", lambda t: t["r"], lambda t: "cur" not in t),
        ("거래량 ×5 청산", lambda t: t["shadow"][5]["r"], lambda t: t["shadow"][5]["label"] != "진행"),
        ("거래량 ×2 청산", lambda t: t["shadow"][2]["r"], lambda t: t["shadow"][2]["label"] != "진행"),
    ):
        rs = [rf(t) for t in trades]
        done_rs = [rf(t) for t in trades if done(t)]
        modes[name] = {"total": sum(rs), "n": len(rs), "n_done": len(done_rs), "n_open": len(rs) - len(done_rs),
                       "avg_done": (sum(done_rs) / len(done_rs)) if done_rs else 0.0}
    buckets = []
    for lab in BUCKET_ORDER:
        ts = [t for t in trades if t["bucket"] == lab]
        if not ts:
            continue
        buckets.append({"bucket": lab, "n": len(ts), "r": sum(t["r"] for t in ts),
                        "avg": sum(t["r"] for t in ts) / len(ts),
                        "r5": sum(t["shadow"][5]["r"] for t in ts),
                        "r2": sum(t["shadow"][2]["r"] for t in ts)})
    return {"modes": modes, "buckets": buckets, "n": len(trades)}


def main() -> None:
    r = compute(SEED)
    cash, equity, realized, unreal = r["cash"], r["equity"], r["realized"], r["unreal"]
    closed, pos = r["closed"], r["open"]
    pos_lines = [f"    {p['name']}: {p['shares']}주 @ {p['fill']:,.0f} → {p['cur']:,.0f} ({p['pnl']:+,.0f}원)"
                 for p in pos]
    skipped = r["skipped"]
    print(f"★ 가상 계좌 (계약 픽 · {START_DATE}~ · 종자돈 {SEED:,}원 · 리스크 {r['risk']:,}원/건 · 비용 0.3%)")
    print(f"  평가금액 {equity:,.0f}원 ({(equity/SEED-1)*100:+.2f}%) = 현금 {cash:,.0f} + 보유 {equity-cash:,.0f}")
    print(f"  실현손익 {realized:+,.0f}원 · 미실현 {unreal:+,.0f}원")
    n_trade = [c for c in closed if c["label"] != "미체결 소멸"]
    print(f"  종결 {len(n_trade)}건 (목표 {sum(1 for c in n_trade if c['label']=='목표')} · "
          f"손절 {sum(1 for c in n_trade if c['label']=='손절')} · "
          f"기한 {sum(1 for c in n_trade if c['label']=='기한 청산')}) · "
          f"미체결 소멸 {sum(1 for c in closed if c['label']=='미체결 소멸')}건")
    for c in n_trade:
        print(f"    {c['date']} {c['name']}: {c['pnl']:+,.0f}원 ({c['label']})")
    if pos_lines:
        print("  보유 중:")
        for ln in pos_lines:
            print(ln)
    ss = summary(r)
    print(f"  그림자 청산 병기 (표시 전용 · 체결 {ss['n']}건 동일, 청산만 다름):")
    for name, m in ss["modes"].items():
        print(f"    {name:12s}: 합계 {m['total']:+.2f}R · 종결 {m['n_done']}건 평균 {m['avg_done']:+.2f}R · 진행 {m['n_open']}건")
    print("  시총 구간별 (체결가 × 현재 주식수 근사):")
    for b in ss["buckets"]:
        print(f"    {b['bucket']:12s}: {b['n']:2d}건 · 계약 {b['r']:+.2f}R (평균 {b['avg']:+.2f}) · ×5 {b['r5']:+.2f}R · ×2 {b['r2']:+.2f}R")
    for nm, dt, why in skipped:
        print(f"  ⚠ 스킵 {dt} {nm}: {why}")


if __name__ == "__main__":
    main()
