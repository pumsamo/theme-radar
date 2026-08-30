"""수급 관찰 그림자 트랙 — 동반 순매수 지속 종목의 전방 성적 축적 (2026-08-30 사용자 확정).

기록: 최신 수급일 기준 '기관·외인 5일 중 4일 동반 순매수 + 일평균 순매수 1억+ +
  거래대금 30억+' 종목을 candidates(origin='flows', tier='fwatch')에 저장.
채점: 기록 다음 거래일 시가 매수 가정 → 20/60거래일 후 수익률 (손절 없음 — bt_flows ②의
  예측력 검증 스펙 그대로. 60일 도달 시 종결). "사면 됐을까"를 데이터가 답하게 하는 트랙.
계약·픽 규칙과 무관. 저녁 루틴 ⑨단계.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import tickers
from db import connect
from prices_kr import fetch_ohlc

FLOWS = Path(__file__).resolve().parent.parent / "data" / "flows"
HORIZON = 60


def latest_hits():
    code_name = {}
    try:
        for nm, info in tickers.table().items():
            c = info.get("code") if isinstance(info, dict) else None
            if c:
                code_name[c] = nm
    except Exception:  # noqa: BLE001
        pass
    hits = []
    for path in FLOWS.glob("*.json"):
        try:
            fl = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        days = sorted(fl)[-5:]
        if len(days) < 5:
            continue
        win = [fl[d] for d in days]
        both = sum(1 for w in win if w[0] > 0 and w[1] > 0)
        if both < 4:
            continue
        if sum((w[0] + w[1]) * w[2] for w in win) / 5 < 1e8:
            continue
        d20 = sorted(fl)[-20:]
        if sum(fl[d][2] * fl[d][3] for d in d20) / max(1, len(d20)) < 30e8:
            continue
        code = path.stem
        hits.append({"code": code, "name": code_name.get(code, code),
                     "date": days[-1],
                     "sum5": sum((w[0] + w[1]) * w[2] for w in win) / 1e8,
                     "both": both})
    return hits


def main() -> None:
    db = connect()
    hits = latest_hits()
    if hits:
        sig = max(h["date"] for h in hits)
        sig_iso = f"{sig[:4]}-{sig[4:6]}-{sig[6:]}"
        for h in (x for x in hits if x["date"] == sig):
            db.execute(
                """insert or replace into candidates
                   (date, code, name, origin, tier, setup, reason, score, data_status)
                   values (?,?,?,?,?,?,?,?,?)""",
                (sig_iso, h["code"], h["name"], "flows", "fwatch", "동반 순매수 지속",
                 f"동반 {h['both']}/5일 · 5일 순매수 {h['sum5']:,.0f}억", h["sum5"], "ok"))
        db.commit()
        print(f"기록: {sig_iso} {sum(1 for x in hits if x['date'] == sig)}종목")

    rows = db.execute(
        "select date, code, name from candidates where origin='flows' order by date").fetchall()
    codes = sorted({r[1] for r in rows})
    today = _date.today().strftime("%Y%m%d")

    def get(code):
        try:
            return code, fetch_ohlc(code, "20260801", today)
        except Exception:  # noqa: BLE001
            return code, []
    with ThreadPoolExecutor(8) as ex:
        bars_all = dict(ex.map(get, codes))

    done, live = [], []
    for pdate, code, name in rows:
        bars = bars_all.get(code, [])
        d0 = pdate.replace("-", "")
        idx = next((i for i, b in enumerate(bars) if b["date"] > d0), None)
        if idx is None or bars[idx]["open"] <= 0:
            continue
        entry = bars[idx]["open"]
        held = len(bars) - 1 - idx
        if held >= HORIZON:
            ret = (bars[idx + HORIZON]["close"] / entry - 1) * 100
            done.append((name, pdate, ret))
        elif held >= 0:
            ret = (bars[-1]["close"] / entry - 1) * 100
            live.append((name, pdate, ret, held))

    print(f"\n★ 수급 관찰 트랙 (기록 {len(rows)}건 · 60거래일 종결 기준 · 손절 없음 예측력 측정)")
    if done:
        rs = [r for _, _, r in done]
        print(f"  종결 {len(done)}건: 평균 {sum(rs)/len(rs):+.2f}% · "
              f"승률 {sum(1 for r in rs if r > 0)/len(rs):.0%}")
    if live:
        rs = [r for _, _, r, _ in live]
        print(f"  진행 {len(live)}건: 현재 평균 {sum(rs)/len(rs):+.2f}%")
        for name, pdate, ret, held in sorted(live, key=lambda x: -x[2])[:10]:
            print(f"    {pdate} {name}: {ret:+.1f}% (D+{held})")


if __name__ == "__main__":
    main()
