"""사장님 트랙 채점기 — 직접 고른 픽의 전향(forward) 검증.

뉴스 발굴·눌림 매수 같은 '판단'은 백테스트로 검증이 안 된다(과거 데이터에 그 판단이 없다).
유일한 방법은 사기 전에 기록하고, 결과를 채널별로 쌓아 비교하는 것.

기록처: config/map_candidates.json → manual_track.picks
  {"date":"2026-08-13","name":"오이솔루션","entry":25000,"stop":23500,
   "channel":"뉴스","reason":"디일렉 광모듈 수주 기사"}
  · channel: 뉴스 | 눌림 | 지도 | 감   ← 나중에 채널별 성적을 가른다
  · entry가 기록 시점 시세보다 낮으면 지정가 매수(눌림), 높으면 돌파 매수로 자동 판별
  · target 없으면 +2R. 사기 전에 기록 안 한 픽은 넣지 말 것 — 사후 기록은 검증을 오염시킨다.

판정 규약은 replay와 동일: 3일 내 미체결이면 포기, 동시 터치는 손절 우선, 최대 20일.
"""
from __future__ import annotations

import json
import statistics
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import prices_kr
import tickers
from net import RunLog

CFG = Path(__file__).resolve().parent.parent / "config" / "map_candidates.json"
HOLD = 20
FILL_WINDOW = 3


def evaluate(pick: dict, log: RunLog) -> dict:
    name = pick["name"]
    code = tickers.to_code(name)
    if not code:
        return {**pick, "status": "종목코드 미매칭"}

    start = pick["date"].replace("-", "")
    try:
        rows = prices_kr.fetch_ohlc(code, "20260601", _date.today().strftime("%Y%m%d"))
    except Exception as exc:  # noqa: BLE001
        return {**pick, "status": f"시세 실패: {exc}"}
    rows = [r for r in rows if r["date"] >= start]
    if not rows:
        return {**pick, "status": "기록일 이후 거래일 없음 (아직 장 전)"}

    entry, stop = float(pick["entry"]), float(pick["stop"])
    target = float(pick.get("target") or entry + 2 * (entry - stop))
    prev_close = rows[0]["open"]          # 근사: 기록일 시가 기준으로 지정가/돌파 판별
    limit_style = entry < prev_close

    fill = fidx = None
    for k, r in enumerate(rows[:FILL_WINDOW]):
        if limit_style and r["low"] <= entry:
            fill, fidx = min(entry, r["open"]), k
            break
        if not limit_style and r["high"] >= entry:
            fill, fidx = max(entry, r["open"]), k
            break
    if fill is None:
        return {**pick, "status": "미체결" if len(rows) >= FILL_WINDOW else "체결 대기중"}
    if fill <= stop:
        return {**pick, "status": "무효(체결가가 손절선 이하)"}

    risk = fill - stop
    for r in rows[fidx:fidx + HOLD]:
        if r["low"] <= stop:
            return {**pick, "status": "손절", "r": -1.0, "fill": fill}
        if r["high"] >= target:
            return {**pick, "status": "목표달성", "r": round((target - fill) / risk, 2),
                    "fill": fill}
    last = rows[min(fidx + HOLD, len(rows)) - 1]
    r_now = round((last["close"] - fill) / risk, 2)
    done = len(rows) >= fidx + HOLD
    return {**pick, "status": "시간청산" if done else "보유중", "r": r_now, "fill": fill}


def main():
    log = RunLog()
    data = json.loads(CFG.read_text(encoding="utf-8"))
    picks = data.get("manual_track", {}).get("picks", [])
    if not picks:
        print("사장님 트랙에 기록된 픽이 없다.")
        print('형식: {"date":"YYYY-MM-DD","name":"종목명","entry":0,"stop":0,'
              '"channel":"뉴스|눌림|지도|감","reason":"한 줄"}')
        return

    results = [evaluate(p, log) for p in picks]

    print("\n" + "=" * 72)
    print("사장님 트랙 — 직접 고른 픽 채점 (전향 검증)")
    print("=" * 72)
    for r in results:
        rs = f'{r["r"]:+.2f}R' if r.get("r") is not None else ""
        print(f'  [{r.get("channel", "?"):<3}] {r["date"]} {r["name"]:<12} '
              f'진입 {float(r["entry"]):>9,.0f} 손절 {float(r["stop"]):>9,.0f} '
              f'→ {r["status"]:<8} {rs}  ({r.get("reason", "")[:28]})')

    print("\n  ▸ 채널별 성적 (체결분, 보유중은 현재가 기준)")
    by_ch: dict[str, list[float]] = {}
    for r in results:
        if r.get("r") is not None:
            by_ch.setdefault(r.get("channel", "?"), []).append(r["r"])
    if not by_ch:
        print("    아직 체결된 픽 없음")
    for ch, rs in by_ch.items():
        win = sum(1 for x in rs if x > 0) / len(rs) * 100
        note = " (표본 부족 — 15건 이상 쌓여야 판단)" if len(rs) < 15 else ""
        print(f"    {ch:<4} n={len(rs):>3}  승률 {win:5.1f}%  평균 {statistics.mean(rs):+.2f}R{note}")

    print("\n  비교 기준: 시스템 v1 백테스트 +0.171R · 미국신호일 +0.323R")
    print("  ※ 채널별 15건 이상 모이면 '그 채널이 되는지' 판정 가능. 사후 기록 금지.")


if __name__ == "__main__":
    main()
