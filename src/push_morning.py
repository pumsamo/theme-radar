"""로컬 아침 런 결과를 GitHub에 발행 — 카톡(Pages latest.txt)이 최신을 받게 (2026-08-31).

배경: 클라우드 크론이 상습 지각(8/27 4h·8/28 8h·8/31 1.5h)이라 로컬 07:30이 사실상
주력인데, 로컬은 out/에만 쓰고 안 올려서 07:40 카톡이 옛 브리핑을 읽는 구멍이 있었다.
이 스크립트가 그 구멍을 막는다: DB 덤프 → out/*.html·txt를 docs/로 복사 → 커밋 → 푸시.
core.sql에 로컬 픽이 실려 올라가므로, 늦게 도는 클라우드도 그 픽을 이어받는다
(8/27식 덮어쓰기 사고의 근본 차단). run_morning.bat이 성공 시 호출.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# encoding 명시 필수: 미지정 시 Windows cp949로 자식 출력을 읽다가 한글 UTF-8 바이트에서
# UnicodeDecodeError → stdout=None → crash (9/3 첫 실전에서 발행 전체가 죽은 원인).
def sh(*args, timeout=180):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def main() -> int:
    # ① DB → core.sql
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "src" / "db_text.py"), "dump"],
                       cwd=ROOT / "src", capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print((r.stdout or "").strip() or (r.stderr or "").strip())

    # ② out → docs (워크플로의 복사 단계와 동일)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    n = 0
    for p in (ROOT / "out").glob("*"):
        if p.suffix in (".html", ".txt"):
            shutil.copy2(p, docs / p.name)
            n += 1
    print(f"docs 복사 {n}건")

    # ③ pull → commit → push (재시도)
    sh("git", "pull", "--ff-only")
    sh("git", "add", "docs", "data/core.sql")
    msg = f"장전 브리핑 {_date.today().isoformat()} (로컬 07:30)"
    c = sh("git", "commit", "-m", msg)
    if "nothing to commit" in (c.stdout + c.stderr):
        print("변경 없음 — 푸시 생략")
        return 0
    for i in range(4):
        p = sh("git", "push", "origin", "main", timeout=240)
        if p.returncode == 0:
            print("푸시 완료")
            return 0
        time.sleep(20)
    print("푸시 실패 — 저녁 루틴에서 재시도됨")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
