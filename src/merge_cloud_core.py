"""클라우드(theme-radar-bot)가 origin/main의 data/core.sql에 넣은 행을 로컬 radar.db에 합친다.
저녁 루틴의 db_text dump가 로컬 DB만으로 core.sql을 다시 쓰면 클라우드가 낮에 넣은 행(미국 스냅샷·뉴스 등)이 지워지기 때문.
- 클라우드에만 있는 행은 INSERT OR IGNORE (기본키가 같은 로컬 행은 로컬이 더 최신이므로 그대로 둔다)
- AUTOINCREMENT 순번은 큰 쪽으로 맞춘다
사용: git fetch 뒤  python src/merge_cloud_core.py  →  python src/db_text.py dump (run_evening.sh 머리말 참고)"""
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sql = subprocess.run(["git", "-C", str(ROOT), "show", "origin/main:data/core.sql"], capture_output=True, check=True).stdout.decode("utf-8")
cloud = sqlite3.connect(":memory:")
cloud.executescript(sql)
local = sqlite3.connect(ROOT / "data" / "radar.db")
total = 0
for (t,) in cloud.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
    if not local.execute("select 1 from sqlite_master where name=?", (t,)).fetchone():
        print("로컬에 없는 표 — 건너뜀:", t)
        continue
    have = set(local.execute(f"select * from {t}").fetchall())
    extra = [r for r in cloud.execute(f"select * from {t}").fetchall() if r not in have]
    if not extra:
        continue
    before = local.total_changes
    local.executemany(f"insert or ignore into {t} values ({','.join('?' * len(extra[0]))})", extra)
    added = local.total_changes - before
    total += added
    print(f"{t}: 클라우드에만 {len(extra)}행 → {added}행 추가" + ("" if added == len(extra) else f" (나머지 {len(extra) - added}행은 같은 키의 로컬 행 유지)"))
for name, seq in cloud.execute("select name, seq from sqlite_sequence").fetchall():
    cur = local.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()
    if cur and cur[0] < seq:
        local.execute("update sqlite_sequence set seq=? where name=?", (seq, name))
local.commit()
print("합계 추가", total)
