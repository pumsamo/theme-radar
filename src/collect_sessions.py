"""세션 분리 일봉 보존기 — 정규장(~15:30)과 애프터마켓(15:31~20:00)을 분봉에서 갈라 저장 (2026-09-19 신설).

배경: 2026-09-14부터 네이버·다음 일봉(fchart·siseJson·api.stock·daum)이 전부 애프터마켓 체결을 포함한다.
  일봉 '종가' = 20:00 마지막 체결가, 고가·저가·거래량도 애프터마켓 포함. (9/11까지는 분봉이 15:30에서 끝남)
  예: 세아베스틸지주 9/18 일봉 고가 49,000은 16:02 애프터마켓 체결(정규장 고가 46,200),
      삼성전자 9/17 일봉 종가 256,000 vs 정규장 종가 252,500.
  규칙·백테스트는 전부 정규장 일봉으로 만들어졌으므로, 차이를 재려면 정규장 값을 따로 남겨야 한다.
  분봉은 최근 6거래일만 제공되므로 주 1~2회는 돌려야 빈 날이 안 생긴다.

소스: fchart.stock.naver.com/sise.nhn?timeframe=minute (분봉은 종가·누적거래량만 제공 → 고가·저가는 분 종가 기준 근사,
  실제 일중 고저보다 1~2틱 안쪽일 수 있음. 정규장 종가(15:30 동시호가 체결)와 거래량은 정확)
저장: data/sessions/{YYYYMMDD}.json = {code: [정규_첫가, 정규_고, 정규_저, 정규_종, 정규_거래량, 애프터_고, 애프터_저, 애프터_종, 애프터_거래량]}
  애프터마켓 체결이 없으면 뒤 4칸은 null. 이미 있는 날짜 파일은 종목 단위로 병합(덮어쓰기).
용도: 표시·연구 전용 (계약 규칙·원장 데이터 소스는 건드리지 않는다).
실행: python src/collect_sessions.py
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boot  # noqa: F401
import replay
from net import fetch

OUT = Path(__file__).resolve().parent.parent / "data" / "sessions"
URL = "https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=minute&count=5000&requestType=0"
ROW_RE = re.compile(r'<item data="(\d{12})\|[^|]*\|[^|]*\|[^|]*\|([\d.]+)\|(\d+)"')
REG_END = "1530"


def split_days(code: str) -> dict[str, list]:
    raw = fetch(URL.format(code=code), timeout=20).decode("euc-kr", errors="replace")
    days: dict[str, list] = defaultdict(list)
    for ts, px, vol in ROW_RE.findall(raw):
        days[ts[:8]].append((ts[8:], float(px), int(vol)))
    out = {}
    for d, rows in days.items():
        reg = [r for r in rows if r[0] <= REG_END]
        aft = [r for r in rows if r[0] > REG_END]
        if not reg:
            continue
        rp = [r[1] for r in reg]
        rec = [rp[0], max(rp), min(rp), rp[-1], reg[-1][2]]
        if aft:
            ap = [r[1] for r in aft]
            rec += [max(ap), min(ap), ap[-1], aft[-1][2] - reg[-1][2]]
        else:
            rec += [None, None, None, None]
        out[d] = rec
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    codes = sorted(replay.load_bars().keys())
    print(f"{len(codes)}종목 분봉 수집 시작", flush=True)
    t0 = time.time()
    by_date: dict[str, dict] = defaultdict(dict)
    stat = {"ok": 0, "empty": 0, "err": 0}

    def one(code):
        try:
            return code, split_days(code)
        except Exception:  # noqa: BLE001
            return code, None

    with ThreadPoolExecutor(6) as ex:
        for i, (code, res) in enumerate(ex.map(one, codes), 1):
            if res is None:
                stat["err"] += 1
            elif not res:
                stat["empty"] += 1
            else:
                stat["ok"] += 1
                for d, rec in res.items():
                    by_date[d][code] = rec
            if i % 400 == 0:
                print(f"  {i}/{len(codes)} ({time.time()-t0:.0f}s)", flush=True)
    for d, recs in sorted(by_date.items()):
        p = OUT / f"{d}.json"
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        old.update(recs)
        p.write_text(json.dumps(old, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        n_aft = sum(1 for r in old.values() if r[5] is not None)
        print(f"  · {d}: {len(old)}종목 (애프터마켓 체결 {n_aft}종목)", flush=True)
    print(f"완료 {time.time()-t0:.0f}s: {stat}", flush=True)


if __name__ == "__main__":
    main()
