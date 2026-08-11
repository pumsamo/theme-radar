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

    if not args.skip_seed:
        step(log, "seed", seed_import.run, log=log)

    step(log, "us/baseline", collect_us.baseline, date, log)
    step(log, "us/regime", collect_us.market_regime, date, log)
    step(log, "us/theme", collect_us.theme_moves, date, log)
    step(log, "news", collect_news_kr.run, date, log)
    step(log, "screen", screen.run, date, log)

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
