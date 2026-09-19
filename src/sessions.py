"""세션 분리 값 조회 — data/sessions(collect_sessions.py)로 일봉을 정규장(09:00~15:30) 기준으로 되돌린다 (2026-09-19 신설, 표시 전용).

배경: 2026-09-14부터 네이버·다음 일봉이 애프터마켓(15:30~20:00) 체결을 포함한다 (collect_sessions.py 머리말 참고).
  공식 채점(계약·원장)은 일봉 그대로 둔다 — 사용자 결정 2026-09-19 '(다)': 계약 끝까지 현행 채점 유지, 정규장 기준 값은 현황판에 병기.
  이 모듈은 그 병기용이다. 픽 생성·채점 규칙과 무관.

regularize 규칙 (보수적 — 애프터마켓에서 나온 게 분명한 값만 바꾼다):
  종가·거래량: 정규장 값으로 교체 (15:30 동시호가 체결가·누적 거래량이라 정확)
  고가: 애프터 분봉 고가가 정규 분봉 고가보다 0.3% 넘게 높고, 일봉 고가도 정규 분봉 고가보다 높을 때만 정규 분봉 고가로 교체
  저가: 대칭
  (분봉은 종가만 제공 → 정규장 고저는 1~2틱 안쪽 근사. 0.3% 안쪽 차이는 일봉 값을 그대로 둔다)
  세션 자료가 없는 날(수집 누락·분봉 6거래일 제한)은 일봉 그대로 — missing_days()로 누락 일수를 센다.
"""
from __future__ import annotations

import json
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "data" / "sessions"
AFTER_START = "20260914"   # 일봉에 애프터마켓이 섞이기 시작한 날
TOL = 0.003
_cache: dict[str, dict] | None = None


def load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        _cache = {}
        for p in sorted(DIR.glob("*.json")):
            try:
                _cache[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
    return _cache


def regularize(code: str, bars: list[dict]) -> list[dict]:
    """일봉 리스트의 9/14 이후 봉을 정규장 값으로 되돌린 새 리스트 (원본 불변)."""
    sess = load()
    out = []
    for b in bars:
        rec = sess.get(b["date"], {}).get(code) if b["date"] >= AFTER_START else None
        if not rec or rec[5] is None:
            out.append(b)
            continue
        _first, r_hi, r_lo, r_cl, r_vol, a_hi, a_lo, _a_cl, _a_vol = rec
        nb = dict(b)
        if a_hi > r_hi * (1 + TOL) and b["high"] > r_hi:
            nb["high"] = r_hi
        if a_lo < r_lo * (1 - TOL) and b["low"] < r_lo:
            nb["low"] = r_lo
        nb["close"], nb["volume"] = r_cl, r_vol
        nb["high"] = max(nb["high"], nb["open"], nb["close"])   # 봉 정합성
        nb["low"] = min(nb["low"], nb["open"], nb["close"])
        out.append(nb)
    return out


def missing_days(dates) -> list[str]:
    """9/14 이후 거래일 중 세션 파일이 없는 날 (그날은 병기 값도 일봉 그대로라 공식 값과 같아진다)."""
    sess = load()
    return sorted(d for d in set(dates) if d >= AFTER_START and d not in sess)
