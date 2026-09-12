"""DART 전 시장 공시 목록 수집 — 주요사항보고(B)·거래소공시(I), 월 단위 페이징 (2026-09-12 신설).

키: config/secrets/dart_api_key.txt(한 줄) 또는 환경변수 DART_API_KEY — 사용자가 직접 넣는다. 키 값은 절대 출력하지 않는다.
출력: data/dart/market_{B|I}_{YYYYMM}.json (git 제외, 로컬 캐시) — 필드: rcept_dt, stock_code, corp_cls, corp_name, report_nm, rcept_no
     이미 받은 달은 건너뛰고(이번 달은 다시 받음), 한도 초과(020)면 중단.
실행: python src/collect_dart.py [--from 202409] [--to 202609] [--types BI]
용도: dart_events.py(공시 이벤트 검증)의 전 시장 입력. 하루 한도 20,000회, 2년치 B+I ≈ 800회.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
from check_dart_key import load_key
from net import fetch

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "dart"
API = "https://opendart.fss.or.kr/api/list.json?"
FIELDS = ("rcept_dt", "stock_code", "corp_cls", "corp_name", "report_nm", "rcept_no")


def month_range(a: str, b: str):
    y, m = int(a[:4]), int(a[4:])
    while f"{y}{m:02d}" <= b:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def fetch_month(key: str, ty: str, y: int, m: int) -> list[dict] | None:
    last = _date(y + (m == 12), (m % 12) + 1, 1)
    end = min(_date.fromordinal(last.toordinal() - 1), _date.today())
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"crtfc_key": key, "bgn_de": f"{y}{m:02d}01", "end_de": end.strftime("%Y%m%d"),
                                    "pblntf_ty": ty, "page_no": page, "page_count": 100, "last_reprt_at": "N"})
        try:
            d = json.loads(fetch(API + q, timeout=30).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {ty} {y}-{m:02d} p{page} 호출 실패: {type(exc).__name__}")
            return None
        st = d.get("status")
        if st == "013":
            break
        if st != "000":
            print(f"  ! {ty} {y}-{m:02d} p{page} DART {st}: {d.get('message')}")
            return None if st == "020" else rows
        for r in d.get("list", []):
            rows.append({k: r.get(k, "") for k in FIELDS})
        if page >= int(d.get("total_page") or 1):
            break
        page += 1
        time.sleep(0.15)
    return rows


def main() -> None:
    key = load_key()
    if not key:
        print("키 없음 — config/secrets/dart_api_key.txt 또는 DART_API_KEY")
        return
    args = sys.argv[1:]
    a = args[args.index("--from") + 1] if "--from" in args else "202409"
    b = args[args.index("--to") + 1] if "--to" in args else _date.today().strftime("%Y%m")
    types = args[args.index("--types") + 1] if "--types" in args else "BI"
    OUT.mkdir(parents=True, exist_ok=True)
    cur = _date.today().strftime("%Y%m")
    tasks = [(ty, y, m) for ty in types for y, m in month_range(a, b)
             if not (OUT / f"market_{ty}_{y}{m:02d}.json").exists() or f"{y}{m:02d}" == cur]
    print(f"수집 대상 {len(tasks)}개월분 (병렬 4)", flush=True)

    def one(t):
        ty, y, m = t
        rows = fetch_month(key, ty, y, m)
        if rows is None:
            return t, None
        (OUT / f"market_{ty}_{y}{m:02d}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"  · {ty} {y}-{m:02d}: {len(rows)}건", flush=True)
        return t, len(rows)
    total, failed = 0, []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        for t, n in ex.map(one, tasks):
            if n is None:
                failed.append(t)
            else:
                total += n
    print(f"완료: 신규 수집 {total}건 · 파일 {len(list(OUT.glob('market_*.json')))}개" + (f" · 실패 {failed}" if failed else ""))


if __name__ == "__main__":
    main()
