"""상장주식수·시가총액 스냅샷 수집 (2026-09-07 신설) — 시총 필터 검증(연구 큐 ②)의 선행 데이터.

네이버 금융 '시가총액' 페이지(코스피 sosok=0 · 코스닥 sosok=1, 각 ~40쪽)를 긁어
data/mcap.json 에 {code: {name, market, mcap_eok(억), shares(주)}} 로 저장한다.
과거 시총은 종가 × 상장주식수로 복원해 쓴다(주식수 변동 무시 — 근사).
사용: python src/collect_mcap.py
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "mcap.json"
URL = "https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
ROW = re.compile(r'<a href="/item/main\.naver\?code=(\d{6})"[^>]*class="tltle">([^<]+)</a>(.*?)</tr>', re.S)
NUM = re.compile(r'<td class="number">(.*?)</td>', re.S)   # 전일비·등락률 셀은 태그가 중첩돼 있어 안쪽까지 잡는다
TAG = re.compile(r"<[^>]+>")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("euc-kr", errors="replace")


def to_int(s: str) -> int | None:
    s = s.replace(",", "").strip()
    return int(s) if s.lstrip("-").isdigit() else None


def main() -> None:
    out: dict[str, dict] = {}
    for sosok, market in ((0, "KOSPI"), (1, "KOSDAQ")):
        for page in range(1, 60):
            html = fetch(URL.format(sosok=sosok, page=page))
            rows = ROW.findall(html)
            if not rows:
                break
            for code, name, rest in rows:
                nums = [TAG.sub("", x).strip() for x in NUM.findall(rest)]
                # 기본 필드 순서: 현재가, 전일비, 등락률, 액면가, 시가총액(억), 상장주식수(천주), 외국인비율, 거래량, PER, ROE
                if len(nums) < 6:
                    continue
                mcap, shares_k = to_int(nums[4]), to_int(nums[5])
                if mcap is None or shares_k is None:
                    continue
                out[code] = {"name": name.strip(), "market": market, "mcap_eok": mcap, "shares": shares_k * 1000}
            time.sleep(0.3)
        print(f"{market}: 누적 {len(out)}종목")
    OUT.write_text(json.dumps({"asof": date.today().isoformat(), "stocks": out}, ensure_ascii=False), encoding="utf-8")
    caps = sorted(v["mcap_eok"] for v in out.values())
    print(f"저장 {OUT} · {len(out)}종목 · 시총 중앙값 {caps[len(caps)//2]:,}억 · 1,000억 미만 {sum(c < 1000 for c in caps)}종목")


if __name__ == "__main__":
    main()
