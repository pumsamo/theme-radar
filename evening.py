"""저녁 루틴 원버튼 — "오늘 확인하자" 한 마디에 해당하는 전부.

  python evening.py            # 오늘 날짜로
  python evening.py 2026-08-12 # 특정 날짜

순서: ①클라우드 동기화(아침 픽·미국 스냅샷 가져오기) → ②오늘 장 채점 → ③검증 계약 일지.
한 단계가 죽어도 다음 단계는 돈다 — 부분 결과가 아예 없는 것보다 낫다.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    steps = [
        ("① 클라우드 동기화", [sys.executable, "sync_cloud.py"]),
        ("② 오늘 장 채점", [sys.executable, "score_day.py", "--date", day]),
        ("③ 검증 계약 일지", [sys.executable, "replay.py", "--days", "60"]),
        ("④ 저녁 A급 스캔 기록", [sys.executable, "evening_scan.py", day]),
        ("⑤ 저녁 스캔 트랙 채점", [sys.executable, "score_evenscan.py"]),
        ("⑥ 가상 계좌 1,000만", [sys.executable, "ledger.py", "10000000"]),
        ("⑦ 가상 계좌 3,000만", [sys.executable, "ledger.py", "30000000"]),
        ("⑧ 검증 현황판 생성", [sys.executable, "status_page.py"]),
    ]
    for title, cmd in steps:
        print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
        r = subprocess.run(cmd, cwd=SRC)
        if r.returncode != 0:
            print(f"  ! {title} 실패 (code {r.returncode}) — 다음 단계 계속")


if __name__ == "__main__":
    main()
