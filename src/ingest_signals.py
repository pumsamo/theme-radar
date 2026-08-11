"""LLM이 읽은 뉴스 신호를 규칙 층 위에 얹는다 (지시문 8: LLM/규칙 혼합).

규칙 층(collect_news_kr)은 무인증 RSS만 보고 항상 돈다 — 이게 기본선이다.
그보다 나은 판단(순환매 방향, 정책 수혜 종목 추론, 애매한 반전 문구)은 사람이나 LLM이
네이버뉴스 검색·DART를 읽고 JSON으로 떨궈 주면 여기서 합친다.

입력 JSON:
{
  "date": "2026-08-11",
  "signals": [
    {"kr_theme":"로봇·휴머노이드", "direction":"호재",
     "title":"반도체 다음은 바이오·로봇 순환매", "url":"https://...",
     "source":"네이버뉴스", "summary":"코스닥 4일 24%…", "stocks":["엔젤로보틱스"],
     "weight": 2.5}
  ]
}
weight를 생략하면 2.0 — 규칙 층(보통 1.0~2.0)보다 살짝 높게 잡아 판단이 우선되게 한다.
근거 원문(title/url)이 없는 신호는 받지 않는다. 근거 없는 신호는 이 도구에서 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from pathlib import Path

import boot  # noqa: F401
from db import connect
from net import RunLog

VALID_DIRECTIONS = {"호재", "악재", "죽은테마", "중립"}
DEFAULT_WEIGHT = 2.0


def ingest(payload: dict, log: RunLog) -> int:
    date = payload.get("date") or _date.today().isoformat()
    rows, skipped = [], 0

    for s in payload.get("signals", []):
        title, theme = (s.get("title") or "").strip(), (s.get("kr_theme") or "").strip()
        direction = (s.get("direction") or "").strip()
        if not title or not theme:
            skipped += 1
            continue
        if direction not in VALID_DIRECTIONS:
            log.warn("ingest", f"방향값 이상 — 건너뜀: {direction!r} ({title[:30]})")
            skipped += 1
            continue
        if not s.get("url"):
            log.warn("ingest", f"근거 URL 없음 — 건너뜀: {title[:40]}")
            skipped += 1
            continue

        stocks = s.get("stocks")
        rows.append({
            "date": s.get("date") or date,
            "published": s.get("published"),
            "source": (s.get("source") or "LLM").strip(),
            "kr_theme": theme,
            "direction": direction,
            "stocks": ",".join(stocks) if isinstance(stocks, list) else (stocks or None),
            "title": title,
            "summary": (s.get("summary") or "")[:300] or None,
            "url": s["url"],
            "weight": float(s.get("weight", DEFAULT_WEIGHT)),
        })

    with connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO news_signals
               (date, published, source, kr_theme, direction, stocks, title, summary, url, weight)
               VALUES (:date,:published,:source,:kr_theme,:direction,:stocks,:title,:summary,:url,:weight)""",
            rows)
        conn.commit()

    log.ok("ingest", f"신호 {len(rows)}건 반영 · 건너뜀 {skipped}건")
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="LLM 추출 뉴스 신호 반영")
    ap.add_argument("path", nargs="?", help="JSON 파일 경로 (없으면 stdin)")
    args = ap.parse_args()

    raw = Path(args.path).read_text(encoding="utf-8") if args.path else sys.stdin.read()
    log = RunLog()
    n = ingest(json.loads(raw), log)
    print(f"{n}건 반영. 대시보드 갱신: python run_morning.py --skip-seed")
