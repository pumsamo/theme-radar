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

실행: python src/ledger.py  (저녁 루틴 ⑥단계)
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date

import boot  # noqa: F401
from db import connect
from prices_kr import fetch_ohlc

START_DATE = "2026-08-11"
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000   # python ledger.py 30000000
RISK = SEED // 100                                             # 1% 리스크 연동
COST = 0.003
ENTRY_WINDOW = 3
HOLD = 20


def main() -> None:
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
            return code, fetch_ohlc(code, "20260801", _date.today().strftime("%Y%m%d"))
        except Exception:  # noqa: BLE001
            return code, []
    with ThreadPoolExecutor(8) as ex:
        bars_all = dict(ex.map(get, codes))

    cash = SEED
    open_pos = []   # {name, code, shares, fill, stop, target, opened, deadline_idx}
    closed = []     # {name, date, pnl, label}
    skipped = []

    events = []  # (판정일 정렬용) — 픽마다 진입 시도부터 청산까지 시뮬
    for pdate, code, name, entry, stop in picks:
        bars = bars_all.get(code, [])
        d0 = pdate.replace("-", "")
        idx = next((i for i, b in enumerate(bars) if b["date"] > d0), None)
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
        events.append({"name": name, "code": code, "pdate": pdate, "bars": bars,
                       "fill_i": fill_i, "fill": fill_px, "stop": stop,
                       "target": fill_px + 2 * risk_per_share, "shares": shares})

    # 시간순 현금 관리: 체결일 기준 정렬해 현금 한도 적용
    events.sort(key=lambda e: e["bars"][e["fill_i"]]["date"])
    holding: dict[str, str] = {}  # code → 청산일 (그 전엔 같은 종목 재진입 금지)
    for e in events:
        fill_dt = e["bars"][e["fill_i"]]["date"]
        if e["code"] in holding and fill_dt <= holding[e["code"]]:
            skipped.append((e["name"], e["pdate"], "동일 종목 보유 중 — 재진입 스킵"))
            continue
        notional = e["fill"] * e["shares"]
        cap = SEED * 0.2  # 한 종목 최대 20% (집중 위험 상한)
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
        # 청산 탐색
        bars, i0 = e["bars"], e["fill_i"]
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
            closed.append({"name": e["name"], "date": exit_dt,
                           "pnl": proceeds - notional - fee, "label": label})
            holding[e["code"]] = exit_dt
        else:
            e["notional"] = notional
            open_pos.append(e)
            holding[e["code"]] = "99999999"

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

    print(f"★ 가상 계좌 (계약 픽 · {START_DATE}~ · 종자돈 {SEED:,}원 · 리스크 {RISK:,}원/건 · 비용 0.3%)")
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
    for nm, dt, why in skipped:
        print(f"  ⚠ 스킵 {dt} {nm}: {why}")


if __name__ == "__main__":
    main()
