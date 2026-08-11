"""놓친 것 점검 — 오늘 오른 종목을 거꾸로 짚어 "미리 잡을 수 있었나"를 본다.

기존 검증(verify_news*)은 '뉴스 → 결과' 방향이라 **우리 신호가 맞았나(정확도)**를 잰다.
이건 반대 방향이다. **오늘 오른 것 중 몇 개나 미리 잡을 수 있었나(놓친 것)**를 잰다.

결과를 세 갈래로 나눈다. 이 비율이 의사결정을 가른다.
  ⓐ 장 전에 뉴스가 있었고 + 우리 소스에도 잡혔다   → 시스템이 작동한 경우
  ⓑ 장 전에 뉴스가 있었는데 + 우리 소스엔 없었다   → **소스를 늘리면 잡힌다**
  ⓒ 장 전에 뉴스 자체가 없었다                    → 뉴스로는 원래 못 잡는다

ⓑ가 많으면 뉴스 매체를 늘리는 게 값어치가 있고, ⓒ가 많으면 뉴스 층에 더 투자할 이유가 없다.
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import boot  # noqa: F401
import collect_news_kr
import tickers
from db import connect
from net import RunLog, fetch

RISE = "https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}&page={page}"
GNEWS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

ROW = re.compile(
    r'<a href="/item/main\.naver\?code=(\d{6})" class="tltle">([^<]+)</a>.*?'
    r'<td class="number">([\d,]+)</td>.*?([+-][\d.]+)%.*?<td class="number">([\d,]+)</td>',
    re.S)

# 종목이 아닌 것 / 뉴스 검증에 부적절한 것
EXCLUDE = re.compile(r"(ETN|ETF|레버리지|인버스|선물|스팩|기업인수목적|리츠|우$|[0-9]우[BC]?$)")

MIN_VALUE_EOK = 50.0     # 오늘 거래대금 하한(억) — 유동성 없는 잡주 제외


def todays_movers(log: RunLog, min_chg: float = 8.0, limit: int = 40) -> list[dict]:
    out: dict[str, dict] = {}
    for sosok in (0, 1):
        for page in (1, 2):
            try:
                html = fetch(RISE.format(sosok=sosok, page=page), timeout=25).decode(
                    "euc-kr", errors="replace")
            except Exception as exc:  # noqa: BLE001
                log.warn("miss", f"급등 페이지 실패 sosok={sosok} p={page}: {exc}")
                continue
            for code, name, price, chg, vol in ROW.findall(html):
                name = name.strip()
                if EXCLUDE.search(name):
                    continue
                p = float(price.replace(",", ""))
                v = float(vol.replace(",", ""))
                value_eok = p * v / 1e8
                c = float(chg)
                if c < min_chg or value_eok < MIN_VALUE_EOK:
                    continue
                out[code] = {"code": code, "name": name, "chg": c,
                             "value_eok": round(value_eok, 1)}
    rows = sorted(out.values(), key=lambda r: -r["chg"])[:limit]
    log.ok("miss", f"오늘 급등 {len(rows)}종목 (+{min_chg}% 이상, 거래대금 {MIN_VALUE_EOK}억 이상)")
    return rows


def news_before(name: str, start: str, end: str) -> list[dict]:
    """장이 열리기 **전**에 나온 기사만. end일 당일 기사는 버린다.

    구글의 before: 는 당일을 걸러주지 않는다. 그대로 두면 "[장중수급포착] ○○ +19% 급등"
    같은 사후 기사가 섞여 들어와 "미리 알 수 있었다"고 착각하게 만든다. 날짜로 직접 자른다.
    """
    q = urllib.parse.quote(f'"{name}" after:{start} before:{end}')
    try:
        root = ET.fromstring(fetch(GNEWS.format(q=q), timeout=25, retries=1))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        if not title or name not in title:
            continue
        raw = it.findtext("pubDate")
        try:
            d = parsedate_to_datetime(raw).strftime("%Y-%m-%d") if raw else ""
        except Exception:  # noqa: BLE001
            continue
        if not d or d >= end:      # ← 당일 이후 기사 제외
            continue
        out.append({"date": d, "title": title,
                    "summary": (it.findtext("description") or "")[:200]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="오늘 오른 종목을 미리 잡을 수 있었나")
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--min-chg", type=float, default=8.0)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--lookback", type=int, default=3, help="장 전 며칠치 뉴스를 볼지")
    args = ap.parse_args()

    log = RunLog()
    d = datetime.strptime(args.date, "%Y-%m-%d").date()
    start = (d - timedelta(days=args.lookback)).isoformat()
    end = d.isoformat()          # before는 미포함 → 당일 장중 기사는 안 들어온다

    movers = todays_movers(log, args.min_chg, args.limit)
    if not movers:
        raise SystemExit("급등 종목을 못 찾았다 — 휴장일이거나 페이지 구조 변경")

    # 우리 시스템이 그 기간에 저장해둔 신호
    with connect() as conn:
        sigs = [dict(r) for r in conn.execute(
            "SELECT kr_theme, direction, stocks, title FROM news_signals "
            "WHERE date >= ? AND date < ?", (start, end))]
        stock_themes = {r["code"]: [t.strip() for t in (r["themes"] or "").split(",") if t.strip()]
                        for r in conn.execute("SELECT code, themes FROM stocks")}

    our_stocks = set()
    for s in sigs:
        for n in (s["stocks"] or "").split(","):
            if n.strip():
                our_stocks.add(n.strip())
    our_good_themes = {s["kr_theme"] for s in sigs if s["direction"] == "호재"}

    log.ok("miss", f"우리가 {start}~{end} 저장한 신호 {len(sigs)}건 "
                   f"(직접 언급 종목 {len(our_stocks)}개 · 호재 테마 {len(our_good_themes)}개)")
    log.ok("miss", f"구글뉴스 조회 {len(movers)}종목")

    def one(m):
        r = news_before(m["name"], start, end)
        time.sleep(0.12)
        return m["code"], r

    found: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for code, arts in pool.map(one, movers):
            found[code] = arts

    lex = collect_news_kr._lex()
    buckets = {"ⓐ": [], "ⓑ": [], "ⓒ": []}

    print("\n" + "=" * 78)
    print(f"놓친 것 점검  {args.date}  —  장 전 뉴스({start}~) 기준")
    print("=" * 78 + "\n")

    for m in movers:
        arts = found.get(m["code"], [])
        good = []
        for a in arts:
            direction, _, _ = collect_news_kr.classify(
                f"{a['title']} {a['summary']}", a["title"], lex)
            if direction == "호재":
                good.append(a)

        ours = m["name"] in our_stocks
        theme_hit = any(t in our_good_themes for t in stock_themes.get(m["code"], []))

        if good and (ours or theme_hit):
            tag = "ⓐ"
        elif good:
            tag = "ⓑ"
        else:
            tag = "ⓒ"
        buckets[tag].append(m)

        mark = "종목 직접" if ours else ("테마만" if theme_hit else "없음")
        print(f"  {tag} {m['name']:<16}{m['chg']:>+7.2f}%  거래대금 {m['value_eok']:>6,.0f}억  "
              f"장전기사 {len(arts):>2}건(호재 {len(good)})  우리 신호: {mark}")
        if good:
            print(f"        └ {good[0]['date']} {good[0]['title'][:58]}")

    n = len(movers)
    print("\n  " + "-" * 74)
    for tag, label in (("ⓐ", "뉴스 있었고 우리도 잡음"),
                       ("ⓑ", "뉴스 있었는데 우리 소스엔 없음"),
                       ("ⓒ", "장 전 뉴스 자체가 없음")):
        v = buckets[tag]
        print(f"  {tag} {label:<28} {len(v):>3}종목 ({len(v)/n*100:4.1f}%)")

    print(f"\n  → ⓑ가 크면 매체를 늘릴 값어치가 있다. ⓒ가 크면 뉴스로는 원래 못 잡는다.")
    print(f"  ※ 하루치다. 며칠 쌓아서 볼 것.")


if __name__ == "__main__":
    main()
