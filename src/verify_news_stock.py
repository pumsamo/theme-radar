"""Q2 재검증 — 종목 단위. 뉴스에 이름이 실린 종목이 다음 거래일에 올랐나.

1차 검증(verify_news.py)의 두 가지 허점을 고쳤다.
  ① 테마 중앙값으로 재서 한 종목의 신호가 수십 종목에 희석됐다 → 종목 단위로 잰다.
  ② 유니버스가 2026-07~08 급등주였다 → 미래를 훔쳐보지 않도록
     **관측 시작 전 3개월의 거래대금**으로 유니버스를 뽑는다.

추가로 나눠 보는 것
  좋은 뉴스가 나도 그날 이미 급등했으면 추격 자리다. 반대로 뉴스는 났는데 그날 조용했으면
  아직 반영 안 된 자리일 수 있다. 이 둘을 갈라서 다음날을 비교한다 —
  실제로 장전에 쓸 수 있는지 없는지가 여기서 갈린다.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import timedelta
from email.utils import parsedate_to_datetime

import boot  # noqa: F401
import collect_news_kr
import tickers
from net import CACHE_DIR, RunLog, cached, fetch

GNEWS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

RANK_START, RANK_END = "20250701", "20250930"   # 유니버스 선정 구간(관측 전)
TEST_START, TEST_END = "20251001", "20260731"   # 검증 구간
TOP_N = 400

# 뉴스 제목에서 오탐이 심한 짧은/일반 종목명
STOP = collect_news_kr.STOP_NAMES | {"제일기획", "한섬", "미래에셋", "이마트", "신세계",
                                     "하나투어", "대한항공", "농심", "오뚜기", "빙그레"}


def load_prices() -> dict[str, list[dict]]:
    out = {}
    for p in (CACHE_DIR / "ohlc").glob("*.json"):
        try:
            bars = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if bars:
            out[p.name.split("_")[0]] = bars
    return out


def build_universe(prices: dict[str, list[dict]], log: RunLog) -> dict[str, str]:
    """관측 구간 이전 3개월 거래대금 상위 종목 → {code: name}. 미래 정보 없음."""
    scored = []
    for code, bars in prices.items():
        seg = [b for b in bars if RANK_START <= b["date"] <= RANK_END]
        if len(seg) < 40:
            continue
        val = statistics.mean(b["close"] * b["volume"] for b in seg)
        scored.append((val, code))
    scored.sort(reverse=True)

    out = {}
    for _, code in scored:
        name = tickers.to_name(code)
        if not name or name in STOP or len(name) < 3:
            continue
        out[code] = name
        if len(out) >= TOP_N:
            break
    log.ok("q2", f"유니버스 {len(out)}종목 (거래대금 상위, {RANK_START}~{RANK_END} 기준)")
    return out


def blocks(start: str, end: str, months: int = 2) -> list[tuple[str, str]]:
    s = _date(int(start[:4]), int(start[4:6]), 1)
    e = _date(int(end[:4]), int(end[4:6]), 1)
    out = []
    while s < e:
        nxt = s
        for _ in range(months):
            nxt = (nxt.replace(day=28) + timedelta(days=6)).replace(day=1)
        out.append((s.isoformat(), min(nxt, e).isoformat()))
        s = nxt
    return out


def fetch_stock_news(name: str, start: str, end: str) -> list[dict]:
    q = urllib.parse.quote(f'"{name}" after:{start} before:{end}')
    try:
        root = ET.fromstring(fetch(GNEWS.format(q=q), timeout=25, retries=1))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        raw = it.findtext("pubDate")
        if not title or not raw or name not in title:
            continue      # 제목에 종목명이 있는 것만 — 확실한 것만 센다
        try:
            d = parsedate_to_datetime(raw).strftime("%Y%m%d")
        except Exception:  # noqa: BLE001
            continue
        out.append({"name": name, "date": d, "title": title,
                    "summary": (it.findtext("description") or "")[:250]})
    return out


CKPT = CACHE_DIR / "gnews_stock_partial.json"


def collect(uni: dict[str, str], log: RunLog) -> list[dict]:
    """2,000번 질의는 몇 분 걸린다. PC가 절전되면 통째로 날아가므로 중간 저장한다."""
    blks = blocks(TEST_START, TEST_END)
    jobs = [(n, s, e) for n in uni.values() for s, e in blks]

    done: dict[str, list[dict]] = {}
    if CKPT.exists():
        try:
            done = json.loads(CKPT.read_text(encoding="utf-8"))
            log.ok("q2", f"체크포인트에서 이어감 — 이미 끝난 질의 {len(done)}건")
        except Exception:  # noqa: BLE001
            done = {}

    todo = [j for j in jobs if f"{j[0]}|{j[1]}" not in done]
    log.ok("q2", f"구글뉴스 질의 {len(todo)}건 남음 (전체 {len(jobs)} = "
                 f"{len(uni)}종목 × {len(blks)}블록)")

    def one(job):
        r = fetch_stock_news(*job)
        time.sleep(0.12)
        return f"{job[0]}|{job[1]}", r

    if todo:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, (key, res) in enumerate(pool.map(one, todo), 1):
                done[key] = res
                if i % 200 == 0:
                    CKPT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                    log.ok("q2", f"{i}/{len(todo)} · 누적 기사 "
                                 f"{sum(len(v) for v in done.values())}")
        CKPT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")

    seen, uniq = set(), []
    for res in done.values():
        for a in res:
            k = (a["name"], a["title"])
            if k not in seen:
                seen.add(k)
                uniq.append(a)
    log.ok("q2", f"기사 {len(uniq)}건")
    return uniq


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    log = RunLog()

    prices = load_prices()
    log.ok("q2", f"일봉 캐시 {len(prices)}종목")
    uni = build_universe(prices, log)
    name2code = {v: k for k, v in uni.items()}

    arts = cached("gnews_stock_10m", ttl_sec=7 * 24 * 3600,
                  producer=lambda: collect(uni, log))

    chg: dict[str, dict[str, float]] = {}
    for code in uni:
        d = {}
        bars = prices[code]
        for a, b in zip(bars, bars[1:]):
            if a["close"] > 0:
                d[b["date"]] = (b["close"] / a["close"] - 1) * 100
        chg[code] = d

    lex = collect_news_kr._lex()
    agg: dict[tuple, dict] = defaultdict(lambda: {"호재": 0, "악재": 0, "죽은테마": 0})
    for a in arts:
        code = name2code.get(a["name"])
        if not code or not (TEST_START <= a["date"] <= TEST_END):
            continue
        direction, _, _ = collect_news_kr.classify(
            f"{a['title']} {a['summary']}", a["title"], lex)
        if direction != "중립":
            agg[(code, a["date"])][direction] += 1

    base = [v for c in uni for d, v in chg[c].items() if TEST_START <= d <= TEST_END]
    bu = sum(1 for v in base if v > 0) / len(base) * 100
    bm = statistics.mean(base)

    def nxt(code: str, d: str) -> float | None:
        later = sorted(x for x in chg[code] if x > d)
        return chg[code][later[0]] if later else None

    def line(vals: list[float], tag: str) -> str:
        if len(vals) < 25:
            return f"    {tag:<22} 표본 부족 ({len(vals)})"
        up = sum(1 for v in vals if v > 0) / len(vals) * 100
        mn = statistics.mean(vals)
        se = math.sqrt(bu / 100 * (1 - bu / 100) / len(vals)) * 100
        z = (up - bu) / se
        p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
        star = "★" if p < 0.05 else (" " if p >= 0.10 else "·")
        return (f"    {tag:<22} n={len(vals):>5,}  상승 {up:5.1f}%  "
                f"평균 {mn:+6.2f}%  z={z:+5.1f}  p={p:.3f} {star}")

    good = [k for k, v in agg.items() if v["호재"] and v["호재"] > v["악재"] + v["죽은테마"]]
    bad = [k for k, v in agg.items() if v["악재"] + v["죽은테마"] > v["호재"]]

    print("\n" + "=" * 78)
    print(f"Q2 재검증 — 종목 단위  ({TEST_START} ~ {TEST_END})")
    print("=" * 78)
    print(f"\n  유니버스 {len(uni)}종목 (관측 전 거래대금 상위) · 기사 {len(arts):,}건")
    print(f"  신호 잡힌 (종목×날짜): 좋은 뉴스 {len(good):,} / 나쁜 뉴스 {len(bad):,}")
    print(f"  기준선: 상승 {bu:.1f}% · 평균 {bm:+.2f}%  (표본 {len(base):,})\n")

    for label, sel in (("좋은 뉴스", good), ("나쁜 뉴스", bad)):
        print(f"  ▸ {label}")
        print(line([chg[c][d] for c, d in sel if d in chg[c]], "당일"))
        print(line([v for v in (nxt(c, d) for c, d in sel) if v is not None], "다음 거래일"))
        print()

    # 핵심: 그날 이미 반응한 뉴스 vs 아직 조용한 뉴스
    print("  ▸ 좋은 뉴스를 '당일 반응'으로 갈라 보면 (장전에 쓸 수 있는지가 여기서 갈린다)")
    for lo, hi, tag in ((5, 99, "당일 +5% 이상 급등"), (2, 5, "당일 +2~5%"),
                        (-2, 2, "당일 조용(-2~+2%)"), (-99, -2, "당일 -2% 이하")):
        sel = [(c, d) for c, d in good if d in chg[c] and lo <= chg[c][d] < hi]
        print(line([v for v in (nxt(c, d) for c, d in sel) if v is not None], tag))

    print("\n  ★ = 우연으로 보기 어려움(p<0.05) · · = 경계(p<0.10) · 공백 = 판단 불가")
    print("  ※ 종목명이 기사 제목에 그대로 실린 건만 셌다. 발행시각은 못 믿어 당일/다음날로만 나눴다.")


if __name__ == "__main__":
    main()
