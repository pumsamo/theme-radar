"""DART 인증키 확인 — 키 값은 절대 출력하지 않는다 (사용자가 직접 설정, 2026-09-12).

키 위치(둘 중 하나): ① 환경변수 DART_API_KEY  ② config/secrets/dart_api_key.txt (한 줄, git 제외 폴더)
실행: python src/check_dart_key.py   → 'OK' 또는 실패 사유만 출력
"""
from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path

import boot  # noqa: F401
from net import fetch

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "config" / "secrets" / "dart_api_key.txt"


def load_key() -> str | None:
    k = os.environ.get("DART_API_KEY", "").strip()
    if not k and KEY_FILE.exists():
        k = KEY_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return k or None


def main() -> None:
    key = load_key()
    if not key:
        print("키 없음 — 환경변수 DART_API_KEY 또는 config/secrets/dart_api_key.txt 에 넣어주세요")
        return
    if len(key) != 40:
        print(f"키 길이 이상({len(key)}자) — DART 인증키는 40자입니다. 앞뒤 공백·따옴표 확인")
        return
    q = urllib.parse.urlencode({"crtfc_key": key, "bgn_de": "20260910", "end_de": "20260911", "pblntf_ty": "B", "page_count": 1})
    try:
        d = json.loads(fetch("https://opendart.fsc.or.kr/api/list.json?" + q, timeout=20).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"호출 실패: {type(exc).__name__}")
        return
    st = d.get("status")
    if st == "000":
        print(f"OK — 주요사항보고 2일치 총 {d.get('total_count')}건 조회 가능")
    else:
        print(f"DART 응답 {st}: {d.get('message')}  (010=미등록 키, 020=한도 초과, 100=파라미터 오류)")


if __name__ == "__main__":
    main()
