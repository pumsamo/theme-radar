"""가상 계좌 현금 처리 점검 — 연구·표시 전용 (2026-10-02 신설).

발견(10/2): 그때까지의 ledger.compute는 체결일 순으로 픽을 처리하면서, 청산이 확인된 포지션의 매도 대금을
'그 픽을 처리하는 순간'(= 체결일 시점)에 현금에 더했다. 실제 매도일은 며칠~몇 주 뒤라
그 사이에 체결되는 다른 픽이 아직 들어오지 않은 돈으로 사졌다(현금 미래 참조).
→ 오래된 포지션이 청산되는 날마다 가상 계좌의 과거 체결 내역이 통째로 바뀌었다.
  (10/1 저녁 기준 체결 50건 vs 10/2 저녁 기준 57건 — 10/1까지의 체결 중 23건이 다름)
수정(10/2 저녁, 사용자 승인): ledger.py가 매도 대금을 매도일 다음 거래일부터 쓰도록 고쳤다(= 아래 strict).

R트랙(무제약 — 검증 계약의 판정 기준)은 현금 제약이 없어 수정 전후가 같다. 이 스크립트가 둘 다 확인한다.

mode:  old = 10/2까지의 방식(매도 대금을 체결일 시점에 미리 사용) / strict = 매도일 다음 거래일부터 사용(현행 ledger.py)
       / sameday = 매도 당일부터 사용(참고)
asof:  YYYYMMDD — 그날까지의 봉·픽만으로 다시 계산(그날 저녁에 돌린 것과 같은 조건)

실행: python src/check_ledger_cash.py                (최근 4거래일 기준일 비교 + ledger.py 현행값과 strict 일치 확인)
      python src/check_ledger_cash.py 20261001 20261002
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date

import boot  # noqa: F401
from db import connect
import ledger
from ledger import COST, ENTRY_WINDOW, FETCH_START, HOLD, START_DATE
from prices_kr import fetch_ohlc

_BARS: dict = {}


def load() -> list:
    db = connect()
    rows = db.execute("""select date, code, name, entry, stop from candidates
                         where tier='pick' and date >= ? and entry is not null and stop is not null
                         order by date""", (START_DATE,)).fetchall()
    seen, picks = set(), []
    for r in rows:
        if (r[0], r[1]) in seen:
            continue
        seen.add((r[0], r[1]))
        picks.append(tuple(r))
    end = _date.today().strftime("%Y%m%d")

    def get(code):
        try:
            return code, fetch_ohlc(code, FETCH_START, end)
        except Exception:  # noqa: BLE001
            return code, []
    with ThreadPoolExecutor(8) as ex:
        _BARS.update(dict(ex.map(get, sorted({p[1] for p in picks}))))
    return picks


def events_asof(picks, risk, asof):
    """ledger.compute와 같은 체결 판정(진입가 터치·갭 시가 체결·주수) — asof까지의 봉·픽만 쓴다."""
    out = []
    for pdate, code, name, entry, stop in picks:
        d0 = pdate.replace("-", "")
        if d0 > asof:
            continue
        bars = [b for b in _BARS.get(code, []) if b["date"] <= asof]
        idx = next((i for i, b in enumerate(bars) if b["date"] >= d0), None)
        if idx is None:
            continue
        fill_i = next((i for i in range(idx, min(idx + ENTRY_WINDOW, len(bars))) if bars[i]["high"] >= entry), None)
        if fill_i is None:
            continue
        fill = max(entry, bars[fill_i]["open"])
        rps = fill - stop
        if rps <= 0 or int(risk / rps) <= 0:
            continue
        out.append({"name": name, "code": code, "bars": bars, "fill_i": fill_i, "fill": fill, "stop": stop,
                    "target": fill + 2 * rps, "shares": int(risk / rps), "fill_dt": bars[fill_i]["date"]})
    out.sort(key=lambda e: e["fill_dt"])
    return out


def find_exit(e):
    bars, i0 = e["bars"], e["fill_i"]
    for b in bars[i0:i0 + HOLD]:
        if b["low"] <= e["stop"]:
            return e["stop"], b["date"], "손절"
        if b["high"] >= e["target"]:
            return e["target"], b["date"], "목표"
    if len(bars) - 1 >= i0 + HOLD - 1:
        last = bars[i0 + HOLD - 1]
        return last["close"], last["date"], "기한"
    return None, None, None


def account(picks, seed, mode, asof):
    cash = seed
    pending, holding, trades = [], {}, []  # pending = (매도일, 금액)
    open_val = 0.0
    for e in events_asof(picks, seed // 100, asof):
        if mode != "old":
            keep = []
            for dt, amt in pending:
                if (dt < e["fill_dt"]) if mode == "strict" else (dt <= e["fill_dt"]):
                    cash += amt
                else:
                    keep.append((dt, amt))
            pending = keep
        if e["code"] in holding and e["fill_dt"] <= holding[e["code"]]:
            continue
        shares = e["shares"]
        if e["fill"] * shares > seed * 0.2:
            shares = int(seed * 0.2 / e["fill"])
        if e["fill"] * shares > cash:
            shares = int(cash / e["fill"])
            if shares <= 0:
                continue
        notional = e["fill"] * shares
        cash -= notional
        px, dt, _ = find_exit(e)
        if px is not None:
            net = px * shares - (notional + px * shares) * COST / 2
            if mode == "old":
                cash += net
            else:
                pending.append((dt, net))
            holding[e["code"]] = dt
        else:
            holding[e["code"]] = "99999999"
            open_val += e["bars"][-1]["close"] * shares
        trades.append((e["fill_dt"], e["name"], shares))
    return cash + sum(a for _, a in pending) + open_val, trades


def rtrack(picks, asof, risk=10_000_000):
    """status_page의 R트랙과 같은 방식(종자돈 10억 → 리스크 1,000만, 주수 절사 영향 거의 없음)."""
    holding, out = {}, []
    for e in events_asof(picks, risk, asof):
        if e["code"] in holding and e["fill_dt"] <= holding[e["code"]]:
            continue
        notional = e["fill"] * e["shares"]
        px, dt, lab = find_exit(e)
        if px is not None:
            pnl = px * e["shares"] - notional - (notional + px * e["shares"]) * COST / 2
            holding[e["code"]] = dt
            out.append((e["fill_dt"], e["name"], lab, dt, 2.0 if lab == "목표" else -1.0 if lab == "손절" else pnl / risk))
        else:
            holding[e["code"]] = "99999999"
            out.append((e["fill_dt"], e["name"], "보유", None, (e["bars"][-1]["close"] - e["fill"]) * e["shares"] / risk))
    return out


def main() -> None:
    picks = load()
    days = sys.argv[1:] or [b["date"] for b in _BARS.get("005930") or next(iter(_BARS.values()))][-4:]
    last = max(b["date"] for bars in _BARS.values() for b in bars[-1:])
    print("━━ ledger.py 현행값 vs strict 재현 (같아야 정상)")
    for seed in (10_000_000, 30_000_000):
        live = ledger.compute(seed)["equity"]
        mine = account(picks, seed, "strict", last)[0]
        print(f"  종자돈 {seed:,}: ledger.py {live:,.0f} · strict {mine:,.0f} · 차이 {live - mine:+,.0f}원 {'✓' if abs(live - mine) < 1 else '✗ 불일치'}")
    for seed in (10_000_000, 30_000_000):
        print(f"\n━━ 가상 계좌 · 종자돈 {seed:,}")
        for asof in days:
            cells = []
            for mode in ("old", "strict", "sameday"):
                eq, tr = account(picks, seed, mode, asof)
                cells.append(f"{mode} {eq:,.0f}({(eq / seed - 1) * 100:+.2f}%) 체결 {len(tr)}")
            print(f"  {asof[4:6]}/{asof[6:]} 기준 | " + " | ".join(cells))
    print("\n━━ 기준일이 하루 지나면 과거 체결(체결일·종목·주수)이 바뀌는가")
    for mode in ("old", "strict", "sameday"):
        for a, b in zip(days, days[1:]):
            ta = set(account(picks, 10_000_000, mode, a)[1])
            tb = {x for x in account(picks, 10_000_000, mode, b)[1] if x[0] <= a}
            print(f"  [{mode}] {a[4:]} → {b[4:]}: 달라진 과거 체결 {len(ta ^ tb)}건")
    print("\n━━ R트랙(무제약 · 판정 기준)")
    prev = prev_d = None
    for asof in days:
        t = rtrack(picks, asof)
        line = f"  {asof[4:6]}/{asof[6:]} 기준: {sum(x[4] for x in t):+.2f}R · 체결 {len(t)}건 · 진행 {sum(1 for x in t if x[2] == '보유')}건"
        if prev is not None:
            a = {(x[0], x[1]) for x in prev}
            b = {(x[0], x[1]) for x in t if x[0] <= prev_d}
            ca = {x for x in prev if x[2] != "보유"}
            cb = {x for x in t if x[2] != "보유" and x[3] <= prev_d}
            line += f" · 과거 체결 차이 {len(a ^ b)}건 · 종결 결과 차이 {len(ca ^ cb)}건"
        print(line)
        prev, prev_d = t, asof


if __name__ == "__main__":
    main()
