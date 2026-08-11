"""themes.json / theme_aliases.json 로더 + raw 테마 표기 정규화.

시드 엑셀과 뉴스에 나오는 표기가 제각각이라(`■ 제약·바이오 관련주 (4종목)`, `제약바이오`,
`바이오`) 이걸 하나의 canonical kr_theme으로 모으는 게 테마 지도의 전제다.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import boot  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config"

_PAREN = re.compile(r"[（(]([^)）]*)[)）]")
_COUNT = re.compile(r"\(\s*\d+\s*종목\s*\)\s*$")


@lru_cache(maxsize=1)
def themes() -> list[dict]:
    data = json.loads((CFG / "themes.json").read_text(encoding="utf-8"))
    return [t for t in data["themes"] if not t.get("_note")]


@lru_cache(maxsize=1)
def _aliases() -> tuple[dict, set]:
    data = json.loads((CFG / "theme_aliases.json").read_text(encoding="utf-8"))
    return data["map"], set(data["_skip"])


@lru_cache(maxsize=1)
def by_name() -> dict[str, dict]:
    return {t["kr_theme"]: t for t in themes()}


def readacross_themes() -> list[dict]:
    return [t for t in themes() if t.get("track") == "readacross" and t.get("us_tickers")]


def strip_label(raw: str) -> tuple[str, str | None]:
    """`■ 데이터센터 관련주 (삼성SDS·AI서버)  (4종목)` → ('데이터센터', '삼성SDS·AI서버')"""
    s = raw.strip().lstrip("■□▪•").strip()
    s = _COUNT.sub("", s).strip()

    catalyst = None
    # 마지막 괄호는 촉매 설명. 단 '반도체(소부장)'처럼 이름 일부인 경우도 있어 둘 다 시도한다.
    found = _PAREN.findall(s)
    if found:
        catalyst = found[-1].strip() or None
    base = _PAREN.sub("", s).strip()
    base = re.sub(r"\s*관련주\s*$", "", base).strip()
    base = re.sub(r"\s*그룹주\s*$", " 그룹", base).strip()
    base = re.sub(r"\s*주\s*$", "", base).strip() if base.endswith("등락주") else base
    return base or s, catalyst


def resolve(raw: str) -> tuple[str | None, str | None]:
    """raw 표기 → (canonical kr_theme, catalyst). 매칭 실패해도 버리지 않고 새 테마로 돌려준다.

    None을 반환하는 건 '개별 등락주'처럼 테마가 아닌 묶음일 때뿐이다.
    """
    if not raw or not str(raw).strip():
        return None, None
    base, catalyst = strip_label(str(raw))
    amap, skip = _aliases()

    probe = base.replace(" ", "")
    for s in skip:
        if s.replace(" ", "") == probe:
            return None, catalyst

    if base in amap:
        return amap[base], catalyst
    for k, v in amap.items():
        if k.replace(" ", "") == probe:
            return v, catalyst

    if base in by_name():
        return base, catalyst

    # 부분일치: '전력기기 및 변압기' 같은 변형을 흡수한다 (긴 별칭 우선).
    for k in sorted(amap, key=len, reverse=True):
        if len(k) >= 3 and k.replace(" ", "") in probe:
            return amap[k], catalyst

    return base, catalyst  # 새 테마로 등록


def keywords_of(kr_theme: str) -> list[str]:
    t = by_name().get(kr_theme)
    return list(t.get("keywords", [])) if t else []


if __name__ == "__main__":
    print(f"config 테마 {len(themes())}개 (read-across {len(readacross_themes())}개)")
    samples = [
        "■ 제약·바이오 관련주  (4종목)",
        "■ 데이터센터 관련주 (삼성SDS·AI서버)  (4종목)",
        "■ 반도체(소부장) 관련주  (9종목)",
        "■ 개별 등락주  (6종목)",
        "■ HLB 그룹주 (리보세라닙 FDA 기대)  (9종목)",
        "■ 통신장비 관련주 (광통신)  (2종목)",
        "■ 정치 관련주 (애국 매수·상폐요건 강화)  (13종목)",
        "■ 초전도체 관련주  (1종목)",
        "지역관련주",
        "로봇&자동차부품",
    ]
    for s in samples:
        print(f"  {s!r:52} -> {resolve(s)}")
