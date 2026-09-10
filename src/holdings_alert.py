"""보유 종목 규칙 대응 알림 — 상태가 바뀐 종목만 출력 (2026-09-10 사용자 요청: "대원전선 조건 채워지면 알려주고 카톡 줘").

status_page.holdings_sheet와 같은 규칙을 config/holdings.json 전 종목에 대입하고, 직전 실행(data/holdings_alert_state.json)과
비교해 라벨이 바뀐 종목만 찍는다. 카톡 발송은 스크립트가 못 하므로(카카오 API 미연결) 저녁 루틴에서 이 출력을 보고
KakaotalkChat-MemoChat(MCP)으로 내가 보낸다. 판단·실행은 본인 — 규칙 대입 결과일 뿐 매매 지시 아님.

규칙: 청산 신호(거래량 ≥ 20일 평균 ×5) > 추매 근거 없음(60일 고점比 ≤ −30% 또는 5일 동반 매도) >
      추매 검토 가능(자리 3/3 + 5일 중 3일↑ 동반 순매수 + 시총 1,000억↑ + 224선 위 또는 224선 없음) > 자리 됨(수급 미확인) > 관찰.
실행: python src/holdings_alert.py            (저녁 루틴, status_page 다음)
      python src/holdings_alert.py --watch 대원전선   (특정 종목의 조건 거리까지 상세 출력)
"""
from __future__ import annotations

import json
import sys
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
from prices_kr import fetch_ohlc

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "holdings_alert_state.json"


def rsi14(cl):
    g = l = 0.0
    for i in range(-14, 0):
        d = cl[i] - cl[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def evaluate(h: dict, mcap: dict) -> dict | None:
    bars = fetch_ohlc(h["code"], "20251001", _date.today().strftime("%Y%m%d"))
    if len(bars) < 60:
        return None
    cl = [b["close"] for b in bars]
    close = cl[-1]
    hi60 = max(b["high"] for b in bars[-60:])
    off = (close / hi60 - 1) * 100
    disp = close / (sum(cl[-20:]) / 20)
    r = rsi14(cl)
    ok = sum((-15 <= off <= -3, 0.95 <= disp <= 1.20, 45 <= r <= 75))
    ma224 = sum(cl[-224:]) / 224 if len(cl) >= 224 else None
    v224 = (close / ma224 - 1) * 100 if ma224 else None
    vol20 = sum(b["volume"] for b in bars[-21:-1]) / 20
    volx = bars[-1]["volume"] / vol20 if vol20 else 0.0
    cap = (mcap.get(h["code"]) or {}).get("mcap_eok") or 0
    both5, sup = 0, "—"
    fp = ROOT / "data" / "flows" / f"{h['code']}.json"
    if fp.exists():
        fl = json.loads(fp.read_text(encoding="utf-8"))
        win = [fl[d] for d in sorted(fl)[-5:]]
        both5 = sum(1 for w in win if w[0] > 0 and w[1] > 0)
        inst, frgn = sum(w[0] for w in win), sum(w[1] for w in win)
        sup = "동반 매수" if inst > 0 and frgn > 0 else ("동반 매도" if inst < 0 and frgn < 0 else "엇갈림")
    stop = h.get("stop")
    if stop and close <= stop:
        rule = f"손절선 이탈 — 종가 {close:,.0f} ≤ {stop:,.0f}"
    elif volx >= 5:
        rule = f"청산 신호 — 거래량 {volx:.1f}배 폭발일"
    elif off <= -30:
        rule = "추매 근거 없음 — 낙폭 과대"
    elif sup == "동반 매도":
        rule = "추매 근거 없음 — 동반 매도 중"
    elif ok == 3 and both5 >= 3 and cap >= 1000 and (v224 is None or v224 >= 0):
        rule = f"추매 검토 가능 — 돌파 확인가 {bars[-1]['high'] * 1.002:,.0f}"
    elif ok == 3:
        rule = "자리 됨, 수급 미확인 — 관찰"
    else:
        rule = "관찰 — 조건 미충족"
    return {"close": close, "off": off, "disp": disp, "rsi": r, "ok": ok, "v224": v224, "volx": volx,
            "both5": both5, "sup": sup, "cap": cap, "hi60": hi60, "rule": rule, "date": bars[-1]["date"]}


def main() -> None:
    conf = json.loads((ROOT / "config" / "holdings.json").read_text(encoding="utf-8"))
    try:
        mcap = json.loads((ROOT / "data" / "mcap.json").read_text(encoding="utf-8"))["stocks"]
    except Exception:  # noqa: BLE001
        mcap = {}
    prev = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    watch = sys.argv[sys.argv.index("--watch") + 1] if "--watch" in sys.argv else None
    cur, changes = {}, []
    targets = [dict(h, _kind="보유") for h in conf["holdings"]] + [dict(w, _kind="관심") for w in conf.get("watchlist", [])]
    for h in targets:
        ev = evaluate(h, mcap)
        if not ev:
            continue
        if h["_kind"] == "관심":  # 신규 매수 후보는 '추매' 대신 '신규 진입' 표현
            ev["rule"] = ev["rule"].replace("추매 검토 가능", "신규 진입 검토 가능").replace("추매 근거 없음", "신규 진입 근거 없음")
        h["name"] = f"{h['name']}({h['_kind']})" if h["_kind"] == "관심" else h["name"]
        cur[h["name"]] = ev["rule"]
        before = prev.get(h["name"])
        if before is not None and before != ev["rule"]:
            changes.append((h["name"], before, ev["rule"], ev))
        if watch and h["name"].split("(")[0] == watch:
            need = ev["hi60"] * 0.85
            print(f"[{watch}] {ev['date']} 종가 {ev['close']:,.0f} · 60일 고점 {ev['hi60']:,.0f} 比 {ev['off']:+.1f}% "
                  f"(자리 조건 −15% = {need:,.0f}, {'충족' if ev['off'] >= -15 else f'{(need / ev['close'] - 1) * 100:+.1f}% 남음'}) · "
                  f"이격 {ev['disp']:.2f} · RSI {ev['rsi']:.0f} · 자리 {ev['ok']}/3 · 동반 {ev['both5']}/5({ev['sup']}) · "
                  f"224선 {(f'{ev['v224']:+.0f}%' if ev['v224'] is not None else '없음')} · 시총 {ev['cap']:,.0f}억 → {ev['rule']}")
    STATE.write_text(json.dumps({"asof": _date.today().isoformat(), **cur}, ensure_ascii=False, indent=2), encoding="utf-8")
    if changes:
        print("★ 규칙 대응 변경 (카톡 알림 대상):")
        for nm, b, a, ev in changes:
            print(f"  {nm}: {b} → {a} (종가 {ev['close']:,.0f}, 고점比 {ev['off']:+.1f}%, 동반 {ev['both5']}/5)")
    else:
        print(f"규칙 대응 변경 없음 ({len(cur)}종목, 상태 저장)")
    for nm, rule in cur.items():
        if rule.startswith(("추매 검토", "신규 진입 검토", "청산 신호", "손절선 이탈")):
            print(f"  ▶ 현재 신호: {nm} — {rule}")


if __name__ == "__main__":
    main()
