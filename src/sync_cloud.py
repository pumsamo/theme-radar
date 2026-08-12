"""클라우드 → 로컬 동기화.

아침 브리핑은 클라우드에서 생성되므로(로컬 PC 꺼짐), 그날의 미국 스냅샷·픽·뉴스 신호는
클라우드 core.sql에만 있다. 저녁 채점(score_day) 전에 이걸 로컬 DB로 끌어와야
"오늘 픽이 터졌나"를 채점할 수 있다. 저녁 루틴의 첫 단계.

머지 규칙: 일자별 테이블 4개만. 같은 키는 클라우드가 이김(그날의 진실은 아침 생성분).
로컬 전용 자산(outcomes 백테스트, stocks 확장분)은 건드리지 않는다.
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import boot  # noqa: F401
from db import connect

ROOT = Path(__file__).resolve().parent.parent
SQL_PATH = ROOT / "data" / "core.sql"


def main() -> None:
    r = subprocess.run(["git", "pull", "--ff-only"], cwd=ROOT,
                       capture_output=True, text=True)
    print("git pull:", (r.stdout or r.stderr).strip().splitlines()[-1])

    cloud = sqlite3.connect(":memory:")
    cloud.executescript(SQL_PATH.read_text(encoding="utf-8"))
    cloud.row_factory = sqlite3.Row

    with connect() as local:
        n = {}
        for table in ("global_baseline", "theme_daily", "candidates"):
            rows = cloud.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                continue
            cols = rows[0].keys()
            ph = ",".join("?" * len(cols))
            cur = local.executemany(
                f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({ph})",
                [tuple(r) for r in rows])
            n[table] = len(rows)
        # news_signals는 id가 자동증가라 id 빼고 UNIQUE 제약으로 중복 제거
        rows = cloud.execute("SELECT date, published, source, kr_theme, direction, stocks, "
                             "title, summary, url, weight FROM news_signals").fetchall()
        if rows:
            local.executemany(
                """INSERT OR IGNORE INTO news_signals
                   (date, published, source, kr_theme, direction, stocks, title, summary, url, weight)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""", [tuple(r) for r in rows])
            n["news_signals"] = len(rows)
        local.commit()
        latest = local.execute(
            "SELECT MAX(date) FROM candidates").fetchone()[0]
    print("동기화:", ", ".join(f"{k} {v}행" for k, v in n.items()))
    print(f"로컬 최신 후보 일자: {latest}")


if __name__ == "__main__":
    main()
