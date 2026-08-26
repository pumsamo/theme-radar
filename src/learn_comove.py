"""동행(co-movement) 학습 — 가격 데이터에서 테마 연관망을 스스로 배운다.

외부 콘텐츠 없이 일봉 캐시만으로 두 가지를 추정한다:
  A-1. 테마별 대장 서열 — 테마 급등일에 누가 가장 세게·꾸준히·큰돈으로 움직였나
  A-2. 지도 밖 연관 후보 — 테마 급등일마다 같이 오르는데 지도엔 없는 종목

방법론 주의 (bt_leader 소급 편향의 교훈):
  · 시장 전체가 오른 날의 착시를 빼기 위해 모든 수익률은 그날 전 종목
    중앙값 대비 초과수익으로 계산한다.
  · 결과는 지도 자동 편입이 아니라 곳간(map_candidates) 검토용 후보 목록이다.
    편입 판단은 사람이 한다 — 계약 기간 규칙 그대로.

실행: python src/learn_comove.py           (전체 구축, 주 1회 금요일 갱신)
출력: data/learned_comove.json + 콘솔 리포트
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
import replay
import tickers
from db import connect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "learned_comove.json"

SPIKE_MIN = 3.0        # 테마 중앙값 초과수익 이 이상인 날만 급등일로 (%)
SPIKE_TOP = 12         # 테마당 최근 급등일 최대 사용 수
SPIKE_NEED = 5         # 급등일이 이보다 적으면 테마 스킵 (표본 부족)
CAND_PART = 0.60       # 후보: 급등일 참여율(초과수익 +2% 이상 비율) 하한
CAND_LIFT = 2.5        # 후보: 급등일 평균 초과수익 하한 (%p)
CAND_VAL = 30e8        # 후보: 최근 20일 평균 거래대금 하한 (원)
LOOKBACK = 500         # 최근 N거래일만 사용 (~2년)


def build_returns(bars_all: dict[str, list[dict]]):
    """code → {date: (초과수익 계산 전 수익률%, 거래대금)} + 날짜별 시장 중앙값."""
    ret: dict[str, dict[str, tuple[float, float]]] = {}
    by_date: dict[str, list[float]] = defaultdict(list)
    for code, bars in bars_all.items():
        bars = bars[-LOOKBACK:]
        d = {}
        for prev, cur in zip(bars, bars[1:]):
            if prev["close"] <= 0:
                continue
            r = (cur["close"] / prev["close"] - 1) * 100
            d[cur["date"]] = (r, cur["close"] * cur["volume"])
            by_date[cur["date"]].append(r)
        if d:
            ret[code] = d
    market = {dt: statistics.median(v) for dt, v in by_date.items() if len(v) >= 200}
    return ret, market


def excess(ret, market, code, dt):
    v = ret.get(code, {}).get(dt)
    if v is None or dt not in market:
        return None
    return v[0] - market[dt]


def val20(bars_all, code):
    bars = bars_all.get(code, [])[-20:]
    if not bars:
        return 0.0
    return sum(b["close"] * b["volume"] for b in bars) / len(bars)


def main() -> None:
    bars_all = replay.load_bars()
    ret, market = build_returns(bars_all)
    print(f"일봉 캐시 {len(bars_all)}종목 · 수익률 산출 {len(ret)}종목 · 유효 거래일 {len(market)}일")

    db = connect()
    db.row_factory = None
    members: dict[str, list[tuple[str, str]]] = defaultdict(list)  # theme → [(name, code)]
    code_theme: dict[str, set[str]] = defaultdict(set)
    for name, code, themes in db.execute(
            "select name, code, themes from stocks where themes is not null and themes != ''"):
        for t in themes.split(","):
            t = t.strip()
            if t:
                members[t].append((name, code))
                code_theme[code].add(t)

    # 지도 밖 종목 이름 조회용 (KRX 상장법인목록)
    code_name = {}
    try:
        for nm, info in tickers.table().items():
            c = info.get("code") if isinstance(info, dict) else None
            if c:
                code_name[c] = nm
    except Exception as e:  # noqa: BLE001
        print(f"※ KRX 목록 조회 실패({e}) — 지도 밖 후보는 코드로만 표기")

    result = {"generated": _date.today().isoformat(),
              "params": {"spike_min": SPIKE_MIN, "cand_part": CAND_PART,
                         "cand_lift": CAND_LIFT, "lookback": LOOKBACK},
              "themes": {}}

    for theme, mem in sorted(members.items()):
        mem = [(n, c) for n, c in mem if c in ret]
        if len(mem) < 3:
            continue
        # 테마 신호 = 그날 소속 종목 초과수익의 중앙값
        day_vals: dict[str, list[float]] = defaultdict(list)
        for _, c in mem:
            for dt in ret[c]:
                e = excess(ret, market, c, dt)
                if e is not None:
                    day_vals[dt].append(e)
        theme_sig = {dt: statistics.median(v) for dt, v in day_vals.items()
                     if len(v) >= max(3, len(mem) // 3)}
        spikes = sorted((dt for dt, s in theme_sig.items() if s >= SPIKE_MIN),
                        key=lambda dt: theme_sig[dt], reverse=True)[:SPIKE_TOP]
        if len(spikes) < SPIKE_NEED:
            continue

        # A-1 대장 서열: 급등일 참여율 × 급등일 평균 거래대금
        leaders = []
        for n, c in mem:
            es = [excess(ret, market, c, dt) for dt in spikes]
            es = [e for e in es if e is not None]
            if len(es) < len(spikes) * 0.5:
                continue
            part = sum(1 for e in es if e >= 3.0) / len(es)
            vals = [ret[c][dt][1] for dt in spikes if dt in ret[c]]
            avg_val = sum(vals) / len(vals) if vals else 0
            leaders.append({"name": n, "code": c, "part": round(part, 2),
                            "avg_excess": round(sum(es) / len(es), 2),
                            "spike_val_억": round(avg_val / 1e8)})
        # 참여율 우선, 동률이면 거래대금 — '대장 = 제일 꾸준히 세게 + 큰돈'
        leaders.sort(key=lambda x: (x["part"], x["spike_val_억"]), reverse=True)

        # A-2 지도 밖 연관 후보
        cands = []
        for c in ret:
            if theme in code_theme.get(c, ()):
                continue
            es = [excess(ret, market, c, dt) for dt in spikes]
            es = [e for e in es if e is not None]
            if len(es) < len(spikes) * 0.7:
                continue
            part = sum(1 for e in es if e >= 2.0) / len(es)
            lift = sum(es) / len(es)
            if part < CAND_PART or lift < CAND_LIFT:
                continue
            if val20(bars_all, c) < CAND_VAL:
                continue
            cands.append({"code": c, "name": code_name.get(c, c),
                          "part": round(part, 2), "lift": round(lift, 2),
                          "other_themes": sorted(code_theme.get(c, ()))})
        cands.sort(key=lambda x: (x["part"], x["lift"]), reverse=True)

        result["themes"][theme] = {
            "n_members": len(mem), "spike_days": spikes,
            "leaders": leaders[:5], "candidates": cands[:8]}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    # 콘솔 리포트
    print(f"\n분석 테마 {len(result['themes'])}개 (소속 3종 미만·급등일 {SPIKE_NEED}일 미만은 제외)")
    for theme, r in sorted(result["themes"].items(),
                           key=lambda kv: -len(kv[1]["candidates"])):
        top = r["leaders"][:3]
        lead_s = " · ".join(f"{x['name']}(참여 {x['part']:.0%}, {x['spike_val_억']}억)"
                            for x in top)
        print(f"\n[{theme}] 소속 {r['n_members']} · 급등일 {len(r['spike_days'])}일")
        print(f"  대장 서열: {lead_s}")
        for c in r["candidates"][:5]:
            tag = f" (지도상 {','.join(c['other_themes'])})" if c["other_themes"] else " (지도 밖 신규)"
            print(f"  연관 후보: {c['name']} 참여 {c['part']:.0%} · 초과 +{c['lift']}%p{tag}")
    print(f"\n저장: {OUT}")
    print("※ 후보는 곳간 검토용 — 지도 편입은 수동 판단. 소급 통계라 대장 서열은 "
          "저녁 스캔 전방 데이터로 계속 재검증할 것.")


if __name__ == "__main__":
    main()
