"""Q2 검증 — 국내 뉴스에 뜬 테마가 실제로 다음 거래일에 올랐나 (지시문 4-4 선행 층).

RSS는 최근 기사만 주지만 구글 뉴스 RSS는 `after:`/`before:` 날짜 검색을 받는다.
그걸로 과거 기사를 테마별·월별로 긁어와 같은 판정 사전을 돌린다.

시각 문제와 그 우회
  구글이 발행시각을 07:00 같은 값으로 뭉개서 준다 → 장전 기사인지 장중 기사인지 모른다.
  그래서 **당일이 아니라 다음 거래일** 등락으로 측정한다.
  뉴스가 주가를 따라간 것(후행)을 선행으로 착각하지 않으려는 것이고,
  마침 이 시스템의 실제 사용법(장전에 어젯밤 뉴스 보고 오늘 후보 뽑기)과도 같다.

남는 한계
  구글 뉴스의 수집 범위는 운영에서 쓰는 매경·연합 RSS와 다르다. 따라서 이건
  "내 뉴스 파이프라인의 성적"이 아니라 "국내 뉴스가 테마의 다음날 등락을 예고하나"에
  대한 답이다. 더 근본적인 질문이긴 하다.
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import boot  # noqa: F401
import collect_news_kr
import themes_cfg
import verify_themes
from net import RunLog, cached, fetch

GNEWS = ("https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko")
MIN_SAMPLE = 25


def _query(keywords: list[str], start: str, end: str) -> str:
    # '관련주' 같은 말은 이미 주가가 움직인 뒤에 붙는 표현이라 넣지 않는다.
    # 테마 자체를 가리키는 말로만 검색해야 선행성 검증이 된다.
    terms = " OR ".join(f'"{k}"' for k in keywords[:4])
    return urllib.parse.quote(f"({terms}) after:{start} before:{end}")


def theme_keywords(theme: str) -> list[str]:
    cfg = themes_cfg.by_name().get(theme)
    kws = [k for k in (cfg.get("keywords", []) if cfg else []) if len(k) >= 3]
    tokens = [t for t in re.split(r"[·\s()]", theme) if len(t) >= 3]
    seen, out = set(), []
    for k in tokens + kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def fetch_month(theme: str, keywords: list[str], start: str, end: str) -> list[dict]:
    url = GNEWS.format(q=_query(keywords, start, end))
    try:
        root = ET.fromstring(fetch(url, timeout=25, retries=1))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        raw = it.findtext("pubDate")
        if not title or not raw:
            continue
        try:
            d = parsedate_to_datetime(raw).strftime("%Y%m%d")
        except Exception:  # noqa: BLE001
            continue
        out.append({"theme": theme, "date": d, "title": title,
                    "summary": (it.findtext("description") or "")[:300]})
    return out


def collect(themes: list[str], months: list[tuple[str, str]], log: RunLog) -> list[dict]:
    jobs = [(t, theme_keywords(t), s, e) for t in themes for s, e in months]
    jobs = [(t, k, s, e) for t, k, s, e in jobs if k]
    log.ok("news-bt", f"구글뉴스 질의 {len(jobs)}건 (테마 {len(themes)} × {len(months)}개월)")

    articles: list[dict] = []

    def one(job):
        t, k, s, e = job
        res = fetch_month(t, k, s, e)
        time.sleep(0.15)  # 예의상 간격
        return res

    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, res in enumerate(pool.map(one, jobs), 1):
            articles += res
            if i % 60 == 0:
                log.ok("news-bt", f"{i}/{len(jobs)} · 누적 기사 {len(articles)}")

    seen, uniq = set(), []
    for a in articles:
        key = (a["theme"], a["title"])
        if key not in seen:
            seen.add(key)
            uniq.append(a)
    log.ok("news-bt", f"기사 {len(uniq)}건 (중복 제거 전 {len(articles)})")
    return uniq


def build_signals(articles: list[dict]) -> dict[tuple, dict]:
    """{(theme, date): {호재수, 죽은테마수, 악재수}}"""
    lex = collect_news_kr._lex()
    agg: dict[tuple, dict] = defaultdict(lambda: {"호재": 0, "악재": 0, "죽은테마": 0})
    for a in articles:
        text = f"{a['title']} {a['summary']}"
        direction, _, _ = collect_news_kr.classify(text, a["title"], lex)
        if direction == "중립":
            continue
        agg[(a["theme"], a["date"])][direction] += 1
    return agg


def next_trading_move(series: dict[str, float], date: str) -> float | None:
    """뉴스가 난 날 다음 거래일의 테마 등락."""
    dates = sorted(d for d in series if d > date)
    return series[dates[0]] if dates else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    args = ap.parse_args()

    log = RunLog()
    kr = verify_themes.kr_theme_series(log)

    today = _date.today()
    months = []
    for i in range(args.months, 0, -1):
        s = (today.replace(day=1) - timedelta(days=31 * i)).replace(day=1)
        e = (s + timedelta(days=32)).replace(day=1)
        months.append((s.isoformat(), e.isoformat()))

    themes = [t for t in kr if theme_keywords(t)]
    articles = cached(f"gnews_{args.months}m",
                      ttl_sec=7 * 24 * 3600,
                      producer=lambda: collect(themes, months, log))
    if not articles:
        raise SystemExit("기사를 못 받았다 — 구글 뉴스 차단 여부 확인")

    agg = build_signals(articles)

    base_vals = [v for s in kr.values() for v in s.values()]
    base_up = sum(1 for v in base_vals if v > 0) / len(base_vals) * 100
    base_mean = sum(base_vals) / len(base_vals)

    # 다음날(선행성)과 당일(동행성)을 같이 잰다.
    # 당일만 크게 오르고 다음날이 밋밋하면, 뉴스는 예고가 아니라 이미 난 일의 보도다.
    good, dead, quiet = [], [], []
    good_same, dead_same = [], []
    for (theme, d), c in agg.items():
        if theme not in kr:
            continue
        mv = next_trading_move(kr[theme], d)
        same = kr[theme].get(d)
        if mv is None:
            continue
        if c["죽은테마"] > c["호재"]:
            dead.append(mv)
            if same is not None:
                dead_same.append(same)
        elif c["호재"] >= 2 and c["호재"] > c["죽은테마"] + c["악재"]:
            good.append(mv)
            if same is not None:
                good_same.append(same)
        elif c["호재"] == 1:
            quiet.append(mv)

    print("\n" + "=" * 70)
    print(f"Q2. 뉴스에 뜬 테마가 다음 거래일에 올랐나?  (최근 {args.months}개월)")
    print("=" * 70)
    print(f"\n  기사 {len(articles):,}건 · 신호가 잡힌 (테마×날짜) {len(agg):,}건")
    print(f"  기준선: 테마가 오른 날 {base_up:.1f}% · 평균 {base_mean:+.2f}%  "
          f"(표본 {len(base_vals):,})\n")

    rows = [("호재 기사 2건 이상", good), ("호재 기사 1건", quiet), ("죽은테마·악재 우세", dead)]
    print(f"  {'그날 뉴스':<20}{'표본':>7}{'다음날 상승확률':>16}{'다음날 평균':>13}{'기준선대비':>12}")
    print("  " + "-" * 68)
    for label, vals in rows:
        if len(vals) < MIN_SAMPLE:
            print(f"  {label:<20}{len(vals):>7}{'표본 부족':>16}")
            continue
        up = sum(1 for v in vals if v > 0) / len(vals) * 100
        mean = sum(vals) / len(vals)
        print(f"  {label:<20}{len(vals):>7,}{up:>15.1f}%{mean:>12.2f}%{mean - base_mean:>+11.2f}%p")

    print(f"\n  [대조] 같은 뉴스가 난 '당일' 테마 등락 — 뉴스가 선행인지 보도인지 가른다")
    for label, vals in (("호재 기사 2건 이상", good_same), ("죽은테마·악재 우세", dead_same)):
        if len(vals) < MIN_SAMPLE:
            continue
        up = sum(1 for v in vals if v > 0) / len(vals) * 100
        mean = sum(vals) / len(vals)
        print(f"  {label:<20}{len(vals):>7,}{up:>15.1f}%{mean:>12.2f}%{mean - base_mean:>+11.2f}%p")

    print("\n  ※ 발행시각이 뭉개져 있어 '당일'이 아니라 '다음 거래일'로 측정했다.")
    print("    구글 뉴스 수집범위는 운영에서 쓰는 매경·연합 RSS와 달라서, 이건")
    print("    '내 뉴스 파이프라인 성적'이 아니라 '국내 뉴스의 예고력'에 대한 답이다.")


if __name__ == "__main__":
    main()
