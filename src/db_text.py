"""클라우드용 DB ↔ 텍스트 변환.

GitHub Actions 러너는 매번 새 컴퓨터라 DB가 안 남는다. 21MB 바이너리를 매일 커밋하면
저장소가 비대해지므로, **핵심 테이블만 SQL 텍스트로** 내리고 다음 실행에서 복원한다.
텍스트는 git이 변경분만 저장해서 가볍다. outcomes(백테스트 22만 행)는 로컬 연구용이라 제외.

  python src/db_text.py dump   # DB → data/core.sql
  python src/db_text.py load   # data/core.sql → DB (DB가 없거나 비어 있을 때만)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import boot  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "radar.db"
SQL_PATH = ROOT / "data" / "core.sql"
DROP = ("outcomes",)          # 제외할 테이블


def dump() -> None:
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(":memory:")
    src.backup(dst)
    src.close()
    for t in DROP:
        dst.execute(f"DELETE FROM {t}")
    dst.commit()
    dst.execute("VACUUM")
    SQL_PATH.write_text("\n".join(dst.iterdump()), encoding="utf-8")
    n = sum(1 for _ in SQL_PATH.open(encoding="utf-8"))
    print(f"dump: {SQL_PATH} ({SQL_PATH.stat().st_size / 1e6:.1f}MB, {n:,}줄)")


def load() -> None:
    if not SQL_PATH.exists():
        print("load: core.sql 없음 — 건너뜀")
        return
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        try:
            n = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
        conn.close()
        if n > 0:
            print(f"load: DB에 이미 데이터 있음(종목 {n}) — 건너뜀")
            return
        DB_PATH.unlink()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SQL_PATH.read_text(encoding="utf-8"))
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    conn.close()
    print(f"load: 복원 완료 (종목 {n})")


if __name__ == "__main__":
    {"dump": dump, "load": load}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: print("dump | load")).__call__()
