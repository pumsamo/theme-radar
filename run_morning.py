"""장전 파이프라인 — 평일 아침 1회.

지시문 7번: "실행 실패 시 조용히 죽지 말고 사유를 남기고 부분 결과라도 저장."
그래서 단계마다 예외를 잡아 RunLog에 남기고 다음 단계로 넘어간다. 마지막에 리포트와
대시보드는 어떤 경우에도 만든다 — 데이터가 비면 '데이터 부재'라고 적힌 화면이 나온다.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date as _date
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

import boot  # noqa: F401,E402
import collect_news_kr  # noqa: E402
import collect_us  # noqa: E402
import dashboard  # noqa: E402
import report  # noqa: E402
import screen  # noqa: E402
import seed_import  # noqa: E402
import viewdata  # noqa: E402
from net import RunLog  # noqa: E402


def _market_closed(date: str) -> str | None:
    """휴장이면 사유 문자열, 개장일이면 None. 주말은 크론이 거르지만 이중 안전망."""
    wd = _date.fromisoformat(date).weekday()
    if wd >= 5:
        return "주말"
    import json
    hol = json.loads((Path(__file__).resolve().parent / "config" / "holidays_kr.json")
                     .read_text(encoding="utf-8"))["dates"]
    return hol.get(date)


def step(log: RunLog, name: str, fn, *args, **kwargs):
    """한 단계가 터져도 사이클을 죽이지 않는다."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warn(name, f"단계 실패 — 건너뛴다: {type(exc).__name__}: {exc}")
        if "--debug" in sys.argv:
            traceback.print_exc()
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="장전 관찰후보 생성")
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--skip-seed", action="store_true", help="시드 엑셀 재적재 건너뛰기")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    date = args.date
    log = RunLog()
    print(f"── 장전 파이프라인 {date} ──")

    closed = _market_closed(date)
    if closed:
        import report as _report
        weekday = "월화수목금토일"[_date.fromisoformat(date).weekday()]
        msg = (f"[장전 브리핑] {date.replace('-', '.')} {weekday} — 오늘 한국장 휴장 ({closed})\n"
               f"{'-' * 58}\n오늘의 픽 없음 — 휴장일. 다음 거래일 아침에 다시 발송.\n")
        _report.OUT.mkdir(parents=True, exist_ok=True)
        (_report.OUT / f"{date}.txt").write_text(msg, encoding="utf-8")
        (_report.OUT / "latest.txt").write_text(msg, encoding="utf-8")
        # 워크플로가 out/index.html 복사를 요구한다 — 휴장 안내 페이지로 채운다
        idx = ("<!doctype html><meta charset='utf-8'>"
               "<meta name='viewport' content='width=device-width,initial-scale=1'>"
               f"<title>장전 브리핑 — 휴장</title>"
               "<body style='font-family:sans-serif;max-width:40rem;margin:4rem auto;"
               "padding:0 1rem;line-height:1.7'>"
               f"<h2>{date.replace('-', '.')} ({weekday}) 오늘 한국장 휴장</h2>"
               f"<p>{closed} — 오늘의 픽 없음. 다음 거래일 아침에 다시 발행됩니다.</p></body>")
        (_report.OUT / "index.html").write_text(idx, encoding="utf-8")
        print(msg)
        return 0

    if not args.skip_seed:
        step(log, "seed", seed_import.run, log=log)

    step(log, "us/baseline", collect_us.baseline, date, log)
    step(log, "us/regime", collect_us.market_regime, date, log)
    step(log, "us/theme", collect_us.theme_moves, date, log)
    step(log, "news", collect_news_kr.run, date, log)

    # 크론 지연 가드 (2026-08-27 사고: 07:25 예약이 11:14에 실행돼 장중 시세로 픽이
    # 만들어졌고, 프리장 픽을 덮어썼다). 개장 후 실행이면 KR 스크리닝만 생략한다 —
    # 미국 스냅샷·뉴스는 장중에도 안 변하므로 위에서 이미 수집했다.
    from datetime import datetime, timedelta, timezone
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    if date == now_kst.date().isoformat() and now_kst.strftime("%H:%M") >= "08:50":
        note = (f"[장전 브리핑] {date.replace('-', '.')} — 지연 실행 감지 "
                f"({now_kst.strftime('%H:%M')} KST)\n{'-' * 58}\n"
                "예약 시각(07:25)을 넘겨 개장 후에 실행됐다. 장중 시세로 만든 픽은\n"
                "프리장 계약과 다른 물건이라 오늘 픽은 생성하지 않는다.\n"
                "미국 스냅샷·뉴스 신호는 정상 수집됨 (저녁 채점에 사용).\n")
        report.OUT.mkdir(parents=True, exist_ok=True)
        (report.OUT / f"{date}.txt").write_text(note, encoding="utf-8")
        (report.OUT / "latest.txt").write_text(note, encoding="utf-8")
        (report.OUT / "index.html").write_text(
            "<!doctype html><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>장전 브리핑 — 지연 실행</title>"
            "<body style='font-family:sans-serif;max-width:40rem;margin:4rem auto;"
            "padding:0 1rem;line-height:1.7'>"
            f"<h2>{date.replace('-', '.')} 지연 실행</h2>"
            f"<p>{now_kst.strftime('%H:%M')} KST 실행 — 장중 데이터 오염 방지를 위해 "
            "오늘 픽은 생성하지 않았습니다.</p></body>", encoding="utf-8")
        print(note)
        return 0

    step(log, "screen", screen.run, date, log)
    step(log, "spot", screen.spot_scan, date, log)

    view = viewdata.build(date, notes=log.dump())
    txt = report.write(view)
    html = dashboard.write(view)

    print("\n" + report.render(view))
    print(f"리포트  : {txt}")
    print(f"대시보드: {html}")
    print(f"최신    : {html.parent / 'index.html'}")

    # 후보가 0개여도 실패는 아니다. 수집 자체가 다 죽었을 때만 실패로 본다.
    if not view["baseline"] and not view["news"] and not view["picks"]:
        log.warn("run", "모든 수집 단계가 비었다 — 네트워크·소스 확인 필요")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
