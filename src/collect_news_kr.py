"""국내 뉴스·공시 → 테마 신호 (지시문 4-4 ①, 선행 층).

집계는 후행이라 늦다. 뉴스가 "뜨는 테마"를 먼저 알려주고, 테마 지도가 "그 테마엔 이 종목"을
알려주는 구조. 근거 원문(제목·출처·URL)을 반드시 같이 저장한다 — 근거 없는 신호는 안 쓴다.

죽은 테마 감지가 이 모듈의 절반이다. "반토막", "물린", "피크아웃"이 잡히면 회피 목록으로
분리해 추격을 막는다. 단 "급락 끝났다"처럼 반전 문구가 붙으면 뒤집는다.
"""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import date as _date
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import boot  # noqa: F401
import themes_cfg
import tickers
from db import connect
from net import RunLog, fetch

CFG = Path(__file__).resolve().parent.parent / "config"

# 2글자 종목명은 일반 명사와 겹쳐 오탐이 심하다. 시드에 있어도 이 목록은 제외한다.
STOP_NAMES = {"두산", "한화", "유니온", "대한", "동원", "삼성", "현대", "우진", "천보",
              "NC", "LS", "SK", "KT", "GS", "CJ", "본느", "HMM", "일신", "삼일"}


def _lex() -> dict:
    return json.loads((CFG / "signal_lexicon.json").read_text(encoding="utf-8"))


def _feeds() -> list[dict]:
    data = json.loads((CFG / "news_sources.json").read_text(encoding="utf-8"))
    return [f for f in data["feeds"] if f.get("enabled")]


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def _pubdate(raw: str | None) -> tuple[str | None, str | None]:
    """(YYYY-MM-DD, 원문 표기) — 파싱 실패해도 죽지 않는다."""
    if not raw:
        return None, None
    try:
        dt = parsedate_to_datetime(raw.strip())
        return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        m = re.search(r"(\d{4})[-./](\d{2})[-./](\d{2})", raw)
        return (f"{m.group(1)}-{m.group(2)}-{m.group(3)}", raw.strip()) if m else (None, raw.strip())


def fetch_feed(feed: dict, log: RunLog) -> list[dict]:
    try:
        root = ET.fromstring(fetch(feed["url"], timeout=25))
    except Exception as exc:  # noqa: BLE001 - 한 소스가 죽어도 나머지는 돈다
        log.warn("news", f"{feed['name']} 수신 실패: {type(exc).__name__}: {exc}")
        return []

    items = []
    for it in root.findall(".//item"):
        title = _clean(it.findtext("title"))
        if not title:
            continue
        pub_date, pub_raw = _pubdate(it.findtext("pubDate"))
        items.append({
            "source": feed["name"],
            "kind": feed["kind"],
            "title": title,
            "summary": _clean(it.findtext("description"))[:400],
            "url": (it.findtext("link") or "").strip(),
            "date": pub_date,
            "published": pub_raw,
        })
    log.ok("news", f"{feed['name']} {len(items)}건")
    return items


# ── 추출 ────────────────────────────────────────────────────────────────

def _theme_keywords() -> dict[str, tuple[list[str], list[str]]]:
    """테마별 (strong, weak) 키워드.

    strong = themes.json에 손으로 고른 키워드 + 테마명 토큰. 특이해서 1개만 걸려도 인정한다.
    weak   = 시드 엑셀의 촉매 문구에서 자동 추출된 것. 잡음이 섞여 2개 이상 걸려야 인정한다.
    '미국'·'실적' 같은 일반어는 아예 뺀다 — 안 그러면 월풀 배당 기사가 '미국·이란 전쟁'으로 잡힌다.
    """
    stop = set(_lex().get("theme_keyword_stop", []))
    strong: dict[str, set[str]] = {}
    weak: dict[str, set[str]] = {}

    with connect() as conn:
        for r in conn.execute("SELECT kr_theme, keywords FROM themes WHERE keywords IS NOT NULL"):
            weak[r["kr_theme"]] = {k.strip() for k in r["keywords"].split(",")
                                   if len(k.strip()) >= 2}

    for t in themes_cfg.themes():
        strong[t["kr_theme"]] = {k for k in t.get("keywords", []) if len(k) >= 2}

    for name in set(list(strong) + list(weak)):
        tokens = {t for t in re.split(r"[·\s()]", name) if len(t) >= 3 and t not in stop}
        strong[name] = (strong.get(name, set()) | tokens) - stop
        weak[name] = weak.get(name, set()) - strong[name] - stop

    return {n: (sorted(strong.get(n, set())), sorted(weak.get(n, set())))
            for n in set(list(strong) + list(weak))}


def _stock_names() -> set[str]:
    names: set[str] = set()
    with connect() as conn:
        names |= {r["name"] for r in conn.execute("SELECT name FROM stocks")}
    for t in themes_cfg.themes():
        names |= set(t.get("kr_stocks", []))
    return {n for n in names if len(n) >= 3 or (len(n) == 2 and n not in STOP_NAMES)} - STOP_NAMES


def classify(text: str, title: str, lex: dict,
             full_text: str | None = None) -> tuple[str, float, list[str]]:
    """(direction, 가중치 보정, 근거 단어들)

    text는 판정 대상 구간(테마가 언급된 절)이지만, 반전 문구만은 기사 전체에서 찾는다.
    "메모리주 급락 끝났다"처럼 부정어와 반전어가 다른 절에 흩어지는 일이 잦기 때문.
    """
    hits_good = [w for w in lex["good"] if w in text]
    hits_bad = [w for w in lex["bad"] if w in text]
    hits_dead = [w for w in lex["dead"] if w in text]
    reversal = [w for w in lex["reversal"] if w in (full_text or text)]

    # "급락 끝났다", "조정 마무리" — 부정 신호 옆에 반전 문구가 있으면 죽은테마가 아니다.
    if hits_dead and reversal:
        hits_dead = []
        hits_good = hits_good + reversal

    if hits_dead:
        direction = "죽은테마"
        evidence = hits_dead
    elif hits_bad and not hits_good:
        direction = "악재"
        evidence = hits_bad
    elif hits_good:
        direction = "호재"
        evidence = hits_good
    else:
        return "중립", 0.0, []

    bump = lex["_weights"]["title_hit"] if any(w in title for w in evidence) else 0.0
    return direction, bump, evidence[:4]


def _name_to_themes() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    with connect() as conn:
        for r in conn.execute("SELECT name, themes FROM stocks WHERE themes IS NOT NULL"):
            out[r["name"]] = {t.strip() for t in r["themes"].split(",") if t.strip()}
    for t in themes_cfg.themes():
        for n in t.get("kr_stocks", []):
            out.setdefault(n, set()).add(t["kr_theme"])
    return out


def extract(items: list[dict], log: RunLog) -> list[dict]:
    lex = _lex()
    theme_kw = _theme_keywords()
    stock_names = sorted(_stock_names(), key=len, reverse=True)
    name_themes = _name_to_themes()
    signals: list[dict] = []

    for it in items:
        text = f"{it['title']} {it['summary']}"
        matched_stocks = [n for n in stock_names if n in text][:6]
        stock_themes = set().union(*(name_themes.get(n, set()) for n in matched_stocks)) \
            if matched_stocks else set()

        # 테마 배정 기준. 본문에 키워드 1개 걸린 것만으로 붙이면 코닝 기사가
        # 'AI 소프트웨어'로 가는 식의 오배치가 난다. 그래서 근거를 하나 더 요구한다:
        #   그 테마 종목이 직접 언급됐거나 / 제목에 키워드가 있거나 /
        #   본문에 강한 키워드 2개 이상 / 약한 키워드 3개 이상.
        themes = set(stock_themes)
        for theme, (strong_kw, weak_kw) in theme_kw.items():
            if any(k in it["title"] for k in strong_kw):
                themes.add(theme)
            elif sum(1 for k in strong_kw if k in text) >= 2:
                themes.add(theme)
            elif any(len(k) >= 4 and k in text for k in strong_kw):
                themes.add(theme)  # '휴머노이드'·'데이터센터'급은 하나만 걸려도 특이하다
            elif sum(1 for k in weak_kw if k in text) >= 3:
                themes.add(theme)
        if not themes:
            continue

        # 한 기사가 여러 테마에 정반대 의미인 경우가 흔하다.
        # ("반도체에 물린 투자자 대피 … K뷰티 ETF 수익률 1위" → 반도체는 죽은테마, K뷰티는 아님)
        # 그래서 테마가 실제로 언급된 절만 골라 그 절로 방향을 판정한다.
        segments = [s for s in re.split(r"[.…!?,·\n]|\s{2,}", text) if s.strip()]

        for theme in themes:
            strong_kw, weak_kw = theme_kw.get(theme, ([], []))
            probes = set(strong_kw) | set(weak_kw) | {
                n for n in matched_stocks if theme in name_themes.get(n, set())}
            scope = " ".join(s for s in segments if any(p in s for p in probes)) or text
            scoped_title = it["title"] if any(p in it["title"] for p in probes) else ""

            direction, bump, evidence = classify(scope, scoped_title, lex, full_text=text)
            if direction == "중립":
                continue

            weight = 1.0 + bump
            if matched_stocks:
                weight += lex["_weights"]["stock_hit"]
            if it["kind"] == "disclosure":
                weight += lex["_weights"]["disclosure"]
            if any(w in text for w in lex["rotation"]):
                weight += lex["_weights"]["rotation"]

            signals.append({
                "date": it["date"] or _date.today().isoformat(),
                "published": it["published"],
                "source": it["source"],
                "kr_theme": theme,
                "direction": direction,
                "stocks": ",".join(matched_stocks) or None,
                "title": it["title"],
                "summary": (it["summary"][:200] or None),
                "url": it["url"] or None,
                "weight": round(weight, 2),
                "_evidence": evidence,
            })

    log.ok("news", f"신호 {len(signals)}건 추출 "
                   f"(호재 {sum(1 for s in signals if s['direction']=='호재')} · "
                   f"죽은테마 {sum(1 for s in signals if s['direction']=='죽은테마')} · "
                   f"악재 {sum(1 for s in signals if s['direction']=='악재')})")
    return signals


def save(signals: list[dict]) -> int:
    saved = 0
    with connect() as conn:
        for s in signals:
            cur = conn.execute(
                """INSERT OR IGNORE INTO news_signals
                   (date, published, source, kr_theme, direction, stocks, title, summary, url, weight)
                   VALUES (:date,:published,:source,:kr_theme,:direction,:stocks,:title,:summary,:url,:weight)""",
                {k: v for k, v in s.items() if not k.startswith("_")})
            saved += cur.rowcount
        conn.commit()
    return saved


def run(date: str, log: RunLog, lookback_days: int = 2) -> list[dict]:
    """장전 실행 기준 최근 N일 뉴스만 본다 (간밤 + 전 거래일)."""
    items: list[dict] = []
    for feed in _feeds():
        items += fetch_feed(feed, log)
    if not items:
        log.warn("news", "수집된 기사가 0건 — 선행 층 없이 진행")
        return []

    cutoff = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=lookback_days)).date()
    fresh = [i for i in items
             if not i["date"] or datetime.strptime(i["date"], "%Y-%m-%d").date() >= cutoff]

    signals = extract(fresh, log)
    saved = save(signals)
    log.ok("news", f"신규 저장 {saved}건 (최근 {lookback_days}일 기사 {len(fresh)}/{len(items)})")
    return signals


def theme_scores(date: str, lookback_days: int = 3) -> list[dict]:
    """테마별 뉴스 점수. 죽은테마는 음수로 눌러 회피 목록으로 분리한다."""
    since = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=lookback_days)).date().isoformat()
    with connect() as conn:
        rows = conn.execute("""
            SELECT kr_theme,
                   SUM(CASE direction WHEN '호재' THEN weight
                                      WHEN '죽은테마' THEN -weight
                                      WHEN '악재' THEN -weight*0.6 ELSE 0 END) score,
                   SUM(direction='호재')      n_good,
                   SUM(direction='죽은테마')   n_dead,
                   SUM(direction='악재')      n_bad,
                   COUNT(*) n
            FROM news_signals WHERE date >= ?
            GROUP BY kr_theme ORDER BY score DESC""", (since,)).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    log = RunLog()
    today = _date.today().isoformat()
    sigs = run(today, log)

    print("\n── 테마별 뉴스 점수 (최근 3일) ──")
    for r in theme_scores(today):
        mark = "⚠ 회피" if r["score"] < 0 else ""
        print(f"  {r['score']:+6.1f}  {r['kr_theme']:<16} "
              f"호재{r['n_good']} 죽은{r['n_dead']} 악재{r['n_bad']}  {mark}")

    print("\n── 죽은 테마 신호 원문 ──")
    seen = set()
    for s in sigs:
        if s["direction"] == "죽은테마" and s["title"] not in seen:
            seen.add(s["title"])
            print(f"  [{s['kr_theme']}] {s['title'][:70]}")
            print(f"     근거어: {s['_evidence']} · {s['source']} · {s['url'][:60] if s['url'] else ''}")
