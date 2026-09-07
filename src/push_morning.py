"""로컬 아침 런 결과를 GitHub에 발행 — 카톡(Pages latest.txt)이 최신을 받게 (2026-08-31).

배경: 클라우드 크론이 상습 지각(8/27 4h·8/28 8h·8/31 1.5h)이라 로컬 07:22가 사실상
주력인데, 로컬은 out/에만 쓰고 안 올려서 07:40 카톡이 옛 브리핑을 읽는 구멍이 있었다.
이 스크립트가 그 구멍을 막는다: DB 덤프 → out/*.html·txt를 docs/로 복사 → 커밋 → 푸시.
core.sql에 로컬 픽이 실려 올라가므로, 늦게 도는 클라우드도 그 픽을 이어받는다
(8/27식 덮어쓰기 사고의 근본 차단). run_morning.bat이 성공 시 호출.

2026-09-07 수리 — 9/4·9/7 아침 모두 `git push`가 240초 멈춘 뒤 TimeoutExpired 예외로 죽어
재시도조차 안 됨(예외를 안 잡았음). 이번 판:
  · 타임아웃을 잡아서 진짜로 재시도한다
  · git에 저속 중단(http.lowSpeed*)·비대화(GIT_TERMINAL_PROMPT=0, GCM_INTERACTIVE=never)를 걸어
    자격증명 창이나 멈춘 전송에서 무한 대기하지 않게 한다
  · origin이 먼저 발행해 non-fast-forward면 병합(충돌은 전부 로컬 우선)하고 다시 민다
  · `--push-only`: 덤프·복사 없이 푸시 단계만 (저녁 루틴·수동 시험용)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
       "PYTHONIOENCODING": "utf-8"}
# 45초 동안 1KB/s 미만이면 curl이 끊는다 — 멈춘 푸시를 타임아웃 전에 실패로 돌려 재시도하게.
GIT = ["git", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=45"]


# encoding 명시 필수: 미지정 시 Windows cp949로 자식 출력을 읽다가 한글 UTF-8 바이트에서
# UnicodeDecodeError → stdout=None → crash (9/3 첫 실전에서 발행 전체가 죽은 원인).
def sh(*args, timeout=180):
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=ENV)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"timeout {timeout}s")


def git(*args, timeout=180):
    return sh(*GIT, *args, timeout=timeout)


def resolve_conflicts() -> bool:
    """origin이 먼저 발행한 날: 충돌 파일은 전부 로컬 우선(DB 덤프·브리핑 페이지가 같은 런에서 나온 것이라
    한쪽으로 통일해야 서로 맞는다). 병합 커밋까지 만든다."""
    conf = [f for f in git("diff", "--name-only", "--diff-filter=U").stdout.split() if f]
    for f in conf:
        git("checkout", "--ours", "--", f)
        git("add", f)
    c = git("commit", "--no-edit")
    return c.returncode == 0 or "nothing to commit" in (c.stdout + c.stderr)


def push_with_retry(n: int = 3) -> bool:
    for i in range(n):
        p = git("push", "origin", "main", timeout=150)
        out = (p.stdout + p.stderr).strip()
        if p.returncode == 0:
            print("푸시 완료")
            return True
        if any(k in out for k in ("non-fast-forward", "fetch first", "rejected")):
            print("origin이 앞서 있음(클라우드 선발행) → 병합 후 재시도")
            git("fetch", "origin", timeout=120)
            m = git("merge", "origin/main", "--no-edit")
            if m.returncode != 0 and not resolve_conflicts():
                git("merge", "--abort")
                print("병합 실패 — 저녁 루틴에서 수동 처리")
                return False
            continue
        print(f"푸시 실패 {i + 1}/{n}: {out[-300:]}")
        time.sleep(15)
    print("푸시 실패 — 저녁 루틴에서 재시도됨")
    return False


def main() -> int:
    if "--push-only" not in sys.argv:
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

        # ③ pull → commit
        git("pull", "--ff-only", timeout=120)
        git("add", "docs", "data/core.sql")
        msg = f"장전 브리핑 {_date.today().isoformat()} (로컬 07:22)"
        c = git("commit", "-m", msg)
        if "nothing to commit" in (c.stdout + c.stderr):
            print("변경 없음 — 푸시 생략")
            return 0

    # ④ push (재시도 · 선발행 병합)
    t0 = time.time()
    ok = push_with_retry()
    print(f"푸시 단계 {time.time() - t0:.0f}초")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
