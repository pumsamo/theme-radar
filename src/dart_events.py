"""DART 공시 이벤트 검증 — 주요사항보고(유상증자·CB·자사주 등) 뒤 주가가 시장 대비 어땠나 (2026-09-12, 연구·표시 전용).

데이터: data/dart/filings_chunk_*.json — 지도 종목(+보유·관심) 712개의 주요사항보고(B) 1년치(2025-09-01~2026-09-12),
        MCP(koreaStock-dart_search_filings)로 종목별 수집. 전 시장이 아니라 **우리 지도 종목 표본**이라는 편향을 명기.
방식: 공시 접수일 D0(휴장이면 다음 거래일). 당일 = D0 종가 수익률(접수 시각을 모르므로 장중·장후 혼재),
      D+1·D+5·D+20 = D0 종가 → D+k 종가 수익률 − 같은 구간 전 종목 k일 수익률 중앙값(시장 대체). 우리 흐름(저녁 확인 → 아침 예약)은 D+1부터가 유효.
      정정공시([기재정정]·[첨부정정])는 원 공시와 중복이라 제외, 같은 종목·유형·날짜는 1건.
출력: data/dart_events.json → 현황판 '국면' 시트 하단 '공시 이벤트' 표. **매수·매도 신호 아님** — 회피 필터·관찰 참고.
실행: python src/dart_events.py
"""
from __future__ import annotations

import io
import json
import re
import statistics
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import replay

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "data" / "dart"
OUT = ROOT / "data" / "dart_events.json"
HORIZONS = (1, 5, 20)

# (유형 라벨, 통설, 제목 정규식) — 순서대로 첫 매칭
TYPES = [
    ("유상증자 결정", "희석·자금난 신호 → 회피 통설 (3자배정은 예외적 급등도)", r"주요사항보고서\(유상증자결정\)|주요사항보고서\(유무상증자결정\)"),
    ("CB·BW·EB 발행 결정", "희석 + 오버행 → 회피 통설", r"전환사채권발행결정|신주인수권부사채권발행결정|교환사채권발행결정"),
    ("자사주 취득 결정", "주주환원 → 수혜 통설", r"자기주식취득결정|자기주식취득신탁계약체결결정"),
    ("자사주 소각 결정", "가장 강한 주주환원 → 수혜 통설", r"자기주식소각결정|주식소각결정"),
    ("자사주 처분 결정", "물량 출회 → 약세 통설", r"자기주식처분결정|자기주식취득신탁계약해지결정"),
    ("무상증자 결정", "단기 급등 통설(권리락 착시)", r"무상증자결정"),
    ("감자 결정", "부실 신호 → 회피", r"감자결정"),
    ("합병·분할 결정", "재료 성격 갈림", r"회사합병결정|회사분할결정|회사분할합병결정|주식교환|주식이전"),
    ("타법인·자산 취득 결정", "신사업 재료 통설", r"타법인주식및출자증권취득결정|타법인주식및출자증권양수결정|유형자산양수결정|영업양수결정|중요한자산양수도결정"),
    ("타법인·자산 처분 결정", "구조조정·현금화", r"타법인주식및출자증권처분결정|타법인주식및출자증권양도결정|유형자산양도결정|영업양도결정"),
    ("CB 만기전 취득", "오버행 해소 통설", r"자기전환사채만기전취득결정|만기전취득"),
    ("자기CB 재매각 결정", "취득했던 CB를 다시 팔아 오버행 재출회 → 약세 통설", r"자기전환사채매도결정|자기신주인수권부사채매도결정"),
    ("CB 콜옵션 행사·지정", "대주주가 콜옵션으로 지분 확보 → 확신 신호 통설", r"전환사채매수선택권행사자지정|제3자의전환사채매수선택권행사|신주인수권부사채매수선택권행사자지정|전환주식매수선택권"),
    ("영구채(신종자본증권) 발행", "자본 보강, 금융·부채 많은 기업 → 중립", r"자본으로인정되는채무증권발행결정"),
    ("소송 제기", "불확실성 → 회피 통설", r"소송등의제기"),
    ("영업정지", "악재 → 회피", r"영업정지"),
]


def classify(name: str) -> str | None:
    for label, _, rx in TYPES:
        if re.search(rx, name):
            return label
    return None


def load_filings() -> list[dict]:
    rows = []
    for p in sorted(SRC_DIR.glob("filings_chunk_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {p.name} 읽기 실패: {exc}")
            continue
        rows += d.get("filings", [])
    return rows


def main() -> None:
    filings = load_filings()
    print(f"공시 {len(filings)}건 로드")
    bars_all = replay.load_bars()
    # 시장 중앙값 일간 수익률 (전 종목)
    ret: dict[str, dict[str, float]] = {}
    for code, bars in bars_all.items():
        bs = bars[-520:]
        ret[code] = {b["date"]: b["close"] / a["close"] - 1 for a, b in zip(bs, bs[1:]) if a["close"] and b["close"]}
    by_date: dict[str, list[float]] = defaultdict(list)
    for m in ret.values():
        for d, r in m.items():
            by_date[d].append(r)
    market = {d: statistics.median(v) for d, v in by_date.items() if len(v) >= 1000}
    kr_dates = sorted(market)

    # 다일 벤치마크: 같은 구간(D0→D+k) 전 종목 k일 수익률의 중앙값 (일간 중앙값을 복리로 쌓으면 왜곡 — 일간 중앙값이 평균 −0.3%라 D+20이 전부 좋아 보임)
    close_by = {code: {b["date"]: b["close"] for b in bars[-520:]} for code, bars in bars_all.items()}
    _wm_cache: dict[tuple[str, str], float | None] = {}

    def window_median(d0: str, dk: str) -> float | None:
        key = (d0, dk)
        if key not in _wm_cache:
            xs = []
            for m in close_by.values():
                a, b = m.get(d0), m.get(dk)
                if a and b:
                    xs.append(b / a - 1)
            _wm_cache[key] = statistics.median(xs) if len(xs) >= 500 else None
        return _wm_cache[key]

    seen = set()
    events: list[dict] = []
    skipped = defaultdict(int)
    for f in filings:
        name = f.get("report_name") or ""
        if name.startswith("[") and "정정" in name.split("]")[0]:
            skipped["정정"] += 1
            continue
        if "종속회사" in name:
            skipped["종속회사"] += 1
            continue
        typ = classify(name)
        if not typ:
            skipped["미분류"] += 1
            continue
        code, d = f.get("stock_code"), f.get("submitted_at")
        key = (code, typ, d)
        if key in seen:
            skipped["중복"] += 1
            continue
        seen.add(key)
        bars = bars_all.get(code)
        if not bars:
            skipped["시세 없음"] += 1
            continue
        dates = [b["date"] for b in bars]
        # D0 = 접수일 이후 첫 거래일 (접수일 포함)
        i0 = next((i for i, x in enumerate(dates) if x >= d), None)
        if i0 is None or i0 == 0 or dates[i0] not in market:
            skipped["구간 밖"] += 1
            continue
        k0 = kr_dates.index(dates[i0])
        c0 = bars[i0]["close"]
        ev = {"code": code, "name": f.get("corp_name"), "type": typ, "date": dates[i0], "filed": d,
              "d0": round((c0 / bars[i0 - 1]["close"] - 1 - market[dates[i0]]) * 100, 2)}
        for h in HORIZONS:
            if i0 + h < len(bars) and k0 + h < len(kr_dates):
                wm = window_median(dates[i0], kr_dates[k0 + h])
                if wm is not None:
                    ev[f"d{h}"] = round(((bars[i0 + h]["close"] / c0 - 1) - wm) * 100, 2)
        if i0 + 1 < len(bars):
            ev["gap1"] = round((bars[i0 + 1]["open"] / c0 - 1) * 100, 2)  # 다음날 시가 (아침 예약 진입 참고)
        events.append(ev)
    print(f"이벤트 {len(events)}건 · 제외 {dict(skipped)}")

    summary = []
    for label, note, _ in TYPES:
        evs = [e for e in events if e["type"] == label]
        if not evs:
            continue
        row = {"type": label, "note": note, "n": len(evs),
               "d0": round(statistics.mean(e["d0"] for e in evs), 2)}
        for h in HORIZONS:
            xs = [e[f"d{h}"] for e in evs if f"d{h}" in e]
            if len(xs) >= 5:
                row[f"d{h}"] = round(statistics.mean(xs), 2)
                row[f"d{h}_med"] = round(statistics.median(xs), 2)
                row[f"d{h}_win"] = round(sum(1 for x in xs if x > 0) / len(xs), 2)
                row[f"d{h}_n"] = len(xs)
        gaps = [e["gap1"] for e in evs if "gap1" in e]
        row["gap1"] = round(statistics.mean(gaps), 2) if gaps else None
        row["examples"] = [f"{e['name']} {e['date'][4:6]}/{e['date'][6:]} D+5 {e.get('d5', float('nan')):+.1f}%" for e in sorted(evs, key=lambda x: x["date"], reverse=True)[:4]]
        summary.append(row)
    summary.sort(key=lambda r: -r["n"])
    out = {"generated": _date.today().isoformat(), "period": "20250901-20260912", "universe": "지도 종목 + 보유·관심 (712)",
           "filings": len(filings), "events": len(events), "skipped": dict(skipped), "summary": summary,
           "events_recent": sorted(events, key=lambda e: e["date"], reverse=True)[:60]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{'유형':<16} {'n':>4} {'당일':>7} {'D+1':>7} {'D+5':>7} {'D+20':>7} {'승률20':>6}  통설")
    for r in summary:
        print(f"{r['type']:<16} {r['n']:>4} {r['d0']:>+7.2f} {r.get('d1', float('nan')):>+7.2f} {r.get('d5', float('nan')):>+7.2f} "
              f"{r.get('d20', float('nan')):>+7.2f} {r.get('d20_win', float('nan')):>6.0%}  {r['note']}")


if __name__ == "__main__":
    main()
