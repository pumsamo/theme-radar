"""투자자별 수급 수집기 — 종목별 외국인·기관 순매매량 (2026-08-30 신설).

소스: finance.naver.com/item/frgn.naver (EUC-KR, 페이지당 20거래일).
저장: data/flows/{code}.json = {date: [기관순매매, 외국인순매매, 종가, 거래량, 외인보유율]}
  날짜 키 YYYYMMDD. 재실행 시 이미 최신인 종목은 건너뛰고, 아니면 새 페이지만 병합.

용도: '세력' 검증 — 바닥권 대량거래 + 기관·외인 순매수 조건부 매집설(bt_flows.py),
  A급 자리 × 수급 결합 연구. 지도 자동 편입·픽 규칙과 무관 (계약 불변).

실행: python src/collect_flows.py [pages]   (기본 13페이지 ≈ 1년)
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boot  # noqa: F401
import replay
from net import fetch

OUT = Path(__file__).resolve().parent.parent / "data" / "flows"
URL = "https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
ROW = re.compile(
    r'<td[^>]*class="tc"><span[^>]*>(\d{4})\.(\d{2})\.(\d{2})</span></td>\s*'
    r'<td[^>]*><span[^>]*>([\d,]+)</span></td>.*?'          # 종가
    r'<td[^>]*><span[^>]*>([\d,]+)</span></td>\s*'          # 거래량
    r'<td[^>]*><span[^>]*>([+\-]?[\d,]+)</span></td>\s*'    # 기관
    r'<td[^>]*><span[^>]*>([+\-]?[\d,]+)</span></td>\s*'    # 외국인
    r'<td[^>]*><span[^>]*>[\d,]+</span></td>\s*'            # 보유주수
    r'<td[^>]*><span[^>]*>([\d.]+)%</span></td>', re.S)


def n(s: str) -> int:
    return int(s.replace(",", "").replace("+", ""))


def fetch_page(code: str, page: int) -> dict:
    raw = fetch(URL.format(code=code, page=page), timeout=15).decode("euc-kr", errors="replace")
    out = {}
    for y, m, d, close, vol, inst, forgn, ratio in ROW.findall(raw):
        out[f"{y}{m}{d}"] = [n(inst), n(forgn), n(close), n(vol), float(ratio)]
    return out


def collect(code: str, pages: int) -> str:
    path = OUT / f"{code}.json"
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}
    try:
        first = fetch_page(code, 1)
        if not first:
            return "빈응답"
        latest = max(first)
        if old and max(old) >= latest:
            return "최신"
        data = dict(old)
        data.update(first)
        for p in range(2, pages + 1):
            page = fetch_page(code, p)
            if not page:
                break
            new_keys = set(page) - set(data)
            data.update(page)
            if not new_keys and old:
                break  # 기존 데이터와 이어짐 — 그만
            time.sleep(0.05)
        path.write_text(json.dumps(data), encoding="utf-8")
        return f"{len(data)}일"
    except Exception as e:  # noqa: BLE001
        return f"오류 {type(e).__name__}"


def main() -> None:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 13
    OUT.mkdir(parents=True, exist_ok=True)
    codes = sorted(replay.load_bars().keys())
    print(f"{len(codes)}종목 × 최대 {pages}페이지 수집 시작", flush=True)
    t0 = time.time()
    done = {"n": 0}

    def one(code):
        r = collect(code, pages)
        done["n"] += 1
        if done["n"] % 200 == 0:
            print(f"  {done['n']}/{len(codes)} ({time.time()-t0:.0f}s)", flush=True)
        return r

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(one, codes))
    from collections import Counter
    cnt = Counter("오류" if r.startswith("오류") else ("빈응답" if r == "빈응답" else "수집")
                  for r in results)
    print(f"완료 {time.time()-t0:.0f}s: {dict(cnt)}", flush=True)


if __name__ == "__main__":
    main()
