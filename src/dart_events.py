"""DART 공시 이벤트 검증 — 공시 뒤 주가가 시장 대비 어땠나 (2026-09-12, 연구·표시 전용).

데이터(우선순위): ① data/dart/market_{B,I}_YYYYMM.json — 전 시장 주요사항보고(B)·거래소공시(I), collect_dart.py(DART 키 필요)
                 ② 없으면 data/dart/filings_chunk_*.json — 지도 종목 712개 주요사항보고 1년치(MCP 종목별 수집, 9/12)
방식: 공시 접수일 D0(휴장이면 다음 거래일). 당일 = D0 종가 수익률 − 그날 전 종목 중앙값(접수 시각을 모르므로 장중·장후 혼재),
      D+1·D+5·D+20 = D0 종가 → D+k 종가 수익률 − 같은 구간 전 종목 k일 수익률 중앙값(시장 대체). 우리 흐름(저녁 확인 → 아침 예약)은 D+1부터가 유효.
      정정공시([기재정정]·[첨부정정])·철회·종속회사·스팩 제외, 같은 종목·유형·날짜는 1건(B와 거래소공시가 겹치면 하나).
출력: data/dart_events.json → 현황판 '국면' 시트 하단 '공시 이벤트' 표. **매수·매도 신호 아님** — 회피 필터·관찰 참고.
실행: python src/dart_events.py
"""
from __future__ import annotations

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

# (유형 라벨, 통설 — 검증 대상, 제목 정규식) — 순서대로 첫 매칭 (구체적인 것 먼저)
TYPES = [
    ("CB 만기전 취득", "오버행 해소 통설", r"자기전환사채만기전취득결정|자기신주인수권부사채만기전취득결정|만기전취득"),
    ("자기CB 재매각 결정", "취득했던 CB를 다시 팔아 오버행 재출회 → 약세 통설", r"자기전환사채매도결정|자기신주인수권부사채매도결정"),
    ("CB 콜옵션 행사·지정", "대주주가 콜옵션으로 지분 확보 → 확신 신호 통설", r"전환사채매수선택권행사자지정|제3자의전환사채매수선택권행사|신주인수권부사채매수선택권행사자지정|전환주식매수선택권"),
    ("CB·BW·EB 발행 결정", "희석 + 오버행 → 회피 통설", r"전환사채권발행결정|신주인수권부사채권발행결정|교환사채권발행결정"),
    ("유상증자 결정", "희석·자금난 신호 → 회피 통설 (3자배정은 예외적 급등도)", r"유상증자결정|유무상증자결정"),
    ("무상증자 결정", "단기 급등 통설(권리락 착시)", r"무상증자결정"),
    ("자사주 취득 결정", "주주환원 → 수혜 통설", r"자기주식취득결정|자기주식취득신탁계약체결결정"),
    ("자사주 소각 결정", "가장 강한 주주환원 → 수혜 통설", r"자기주식소각결정|주식소각결정"),
    ("자사주 처분 결정", "물량 출회 → 약세 통설", r"자기주식처분결정|자기주식취득신탁계약해지결정"),
    ("감자 결정", "부실 신호 → 회피", r"감자결정"),
    ("주식분할(액면분할)", "유동성 확대 → 단기 상승 통설", r"주식분할결정"),
    ("주식병합(액면병합)", "부실 정리 → 약세 통설", r"주식병합결정"),
    ("합병·분할 결정", "재료 성격 갈림", r"회사합병결정|회사분할결정|회사분할합병결정|주식교환|주식이전"),
    ("타법인·자산 취득 결정", "신사업 재료 통설", r"타법인주식및출자증권취득결정|타법인주식및출자증권양수결정|유형자산양수결정|영업양수결정|중요한자산양수도결정"),
    ("타법인·자산 처분 결정", "구조조정·현금화", r"타법인주식및출자증권처분결정|타법인주식및출자증권양도결정|유형자산양도결정|영업양도결정"),
    ("영구채(신종자본증권) 발행", "자본 보강, 금융·부채 많은 기업 → 중립", r"자본으로인정되는채무증권발행결정"),
    ("소송 제기", "불확실성 → 회피 통설", r"소송등의제기"),
    ("영업정지", "악재 → 회피", r"영업정지"),
    ("공급계약 체결", "수주 재료 → 단기 상승 통설", r"단일판매\s*[ㆍ·]?\s*공급계약체결"),
    ("잠정실적 공시", "서프라이즈/쇼크 — 방향은 내용에 달림(제목만으론 미상)", r"영업\(잠정\)실적|잠정실적|매출액또는손익구조"),
    ("투자판단 주요경영사항", "임상·수주·계약 등 재료 통설", r"투자판단관련주요경영사항"),
    ("조회공시 요구(시황변동)", "급등락 뒤 요구 → 이후 되돌림 통설", r"조회공시요구\(현저한시황변동\)"),
    ("조회공시 요구(풍문·보도)", "풍문 확인 요구 — 재료 반쯤 노출", r"조회공시요구\(풍문또는보도\)"),
    ("불성실공시 지정·예고", "신뢰 훼손 → 회피", r"불성실공시법인지정"),
    ("관리종목 지정·실질심사", "회피", r"관리종목지정(?!해제)|상장적격성실질심사"),
    ("최대주주 변경", "경영권 변동 재료", r"최대주주변경"),
    ("신규시설투자", "성장 투자 재료", r"신규시설투자"),
    ("특허권 취득", "재료 통설(효과 미미)", r"특허권취득"),
]
DROP = ("철회", "종속회사", "해제", "취소")


def classify(name: str) -> str | None:
    for label, _, rx in TYPES:
        if re.search(rx, name):
            return label
    return None


def load_filings() -> tuple[list[dict], str]:
    market = sorted(SRC_DIR.glob("market_*.json"))
    rows: list[dict] = []
    if market:
        for p in market:
            try:
                for r in json.loads(p.read_text(encoding="utf-8")):
                    rows.append({"report_name": r.get("report_nm", ""), "submitted_at": r.get("rcept_dt", ""), "stock_code": r.get("stock_code", ""),
                                 "corp_name": r.get("corp_name", ""), "receipt_no": r.get("rcept_no", ""), "corp_cls": r.get("corp_cls", "")})
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {p.name} 읽기 실패: {exc}")
        months = [p.name.split("_")[2][:6] for p in market]
        return rows, f"전 시장 코스피·코스닥 (주요사항보고+거래소공시, {min(months)}~{max(months)})"
    for p in sorted(SRC_DIR.glob("filings_chunk_*.json")):
        try:
            rows += json.loads(p.read_text(encoding="utf-8")).get("filings", [])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {p.name} 읽기 실패: {exc}")
    return rows, "지도 종목 + 보유·관심 (712) 주요사항보고 1년"


def main() -> None:
    filings, universe = load_filings()
    print(f"공시 {len(filings)}건 로드 · 표본 {universe}")
    bars_all = replay.load_bars()
    ret: dict[str, dict[str, float]] = {}
    for code, bars in bars_all.items():
        bs = bars[-560:]
        ret[code] = {b["date"]: b["close"] / a["close"] - 1 for a, b in zip(bs, bs[1:]) if a["close"] and b["close"]}
    by_date: dict[str, list[float]] = defaultdict(list)
    for m in ret.values():
        for d, r in m.items():
            by_date[d].append(r)
    market = {d: statistics.median(v) for d, v in by_date.items() if len(v) >= 1000}
    kr_dates = sorted(market)
    kr_index = {d: i for i, d in enumerate(kr_dates)}

    # 다일 벤치마크: 같은 구간(D0→D+k) 전 종목 k일 수익률의 중앙값 (일간 중앙값을 복리로 쌓으면 −0.3%/일 편향으로 왜곡)
    close_by = {code: {b["date"]: b["close"] for b in bars[-560:]} for code, bars in bars_all.items()}
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
    skipped: dict[str, int] = defaultdict(int)
    for f in filings:
        name = re.sub(r"\s+", " ", f.get("report_name") or "").strip()
        if name.startswith("[") and "정정" in name.split("]")[0]:
            skipped["정정"] += 1
            continue
        if any(k in name for k in DROP):
            skipped["철회·종속·해제"] += 1
            continue
        code = (f.get("stock_code") or "").strip()
        if not code or "스팩" in (f.get("corp_name") or ""):
            skipped["비상장·스팩"] += 1
            continue
        typ = classify(name)
        if not typ:
            skipped["미분류"] += 1
            continue
        d = f.get("submitted_at")
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
        i0 = next((i for i, x in enumerate(dates) if x >= d), None)  # D0 = 접수일 이후 첫 거래일(접수일 포함)
        if i0 is None or i0 == 0 or dates[i0] not in kr_index:
            skipped["구간 밖"] += 1
            continue
        k0 = kr_index[dates[i0]]
        c0 = bars[i0]["close"]
        ev = {"code": code, "name": f.get("corp_name"), "type": typ, "date": dates[i0], "filed": d, "cls": f.get("corp_cls", ""),
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
        if len(evs) < 5:
            continue
        row = {"type": label, "note": note, "n": len(evs), "d0": round(statistics.mean(e["d0"] for e in evs), 2),
               "n_kospi": sum(1 for e in evs if e.get("cls") == "Y"), "n_kosdaq": sum(1 for e in evs if e.get("cls") == "K")}
        for h in HORIZONS:
            xs = [e[f"d{h}"] for e in evs if f"d{h}" in e]
            if len(xs) >= 5:
                row[f"d{h}"] = round(statistics.mean(xs), 2)
                row[f"d{h}_med"] = round(statistics.median(xs), 2)
                row[f"d{h}_win"] = round(sum(1 for x in xs if x > 0) / len(xs), 2)
                row[f"d{h}_n"] = len(xs)
        for cls, tag in (("Y", "kospi"), ("K", "kosdaq")):
            xs = [e["d20"] for e in evs if e.get("cls") == cls and "d20" in e]
            if len(xs) >= 10:
                row[f"d20_{tag}"] = round(statistics.mean(xs), 2)
                row[f"d20_{tag}_win"] = round(sum(1 for x in xs if x > 0) / len(xs), 2)
        gaps = [e["gap1"] for e in evs if "gap1" in e]
        row["gap1"] = round(statistics.mean(gaps), 2) if gaps else None
        row["examples"] = [f"{e['name']} {e['date'][4:6]}/{e['date'][6:]} D+5 {e['d5']:+.1f}%" for e in sorted(evs, key=lambda x: x["date"], reverse=True) if "d5" in e][:4]
        summary.append(row)
    summary.sort(key=lambda r: -r["n"])
    out = {"generated": _date.today().isoformat(), "period": f"{kr_dates[0]}~{kr_dates[-1]}", "universe": universe,
           "filings": len(filings), "events": len(events), "skipped": dict(skipped), "summary": summary,
           "events_recent": sorted(events, key=lambda e: e["date"], reverse=True)[:60]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{'유형':<18} {'n':>5} {'당일':>7} {'D+1':>7} {'D+5':>7} {'D+20':>7} {'중앙20':>7} {'승률20':>6}  코스피/코스닥 승률")
    for r in summary:
        ky = f"{r['d20_kospi_win']:.0%}" if "d20_kospi_win" in r else "—"
        kq = f"{r['d20_kosdaq_win']:.0%}" if "d20_kosdaq_win" in r else "—"
        print(f"{r['type']:<18} {r['n']:>5} {r['d0']:>+7.2f} {r.get('d1', float('nan')):>+7.2f} {r.get('d5', float('nan')):>+7.2f} "
              f"{r.get('d20', float('nan')):>+7.2f} {r.get('d20_med', float('nan')):>+7.2f} {r.get('d20_win', float('nan')):>6.0%}  {ky}/{kq}")


if __name__ == "__main__":
    main()
