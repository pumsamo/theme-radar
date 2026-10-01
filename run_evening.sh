#!/bin/bash
# 저녁 루틴 체인 — 사용(Git Bash):  bash run_evening.sh 2026-10-02
# 2026-10-01: 그동안 대화 세션의 임시 스크립트(evening_MMDD.sh)로 돌리던 순서를 저장소에 고정하고,
#             끝에 차트 보조(표시 전용, src/chart_levels.py)를 붙였다.
# 끝난 뒤 커밋 전에: git fetch → python src/merge_cloud_core.py → python src/db_text.py dump
#   (클라우드 theme-radar-bot이 낮에 core.sql에 넣은 행을 로컬 DB에 합쳐야 덤프가 그 행을 지우지 않는다)
set -u
D="${1:?날짜(YYYY-MM-DD)를 인자로 넣어줘}"
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
PY="${PY:-/c/Users/pumsa_yvvjwu4/AppData/Local/Programs/Python/Python312/python.exe}"
DART_FROM="$(date -d "${D:0:7}-01 -1 month" +%Y%m)"   # 전달부터 다시 훑어 늦게 올라온 공시까지 받는다
run() { echo "################ $*"; "$PY" "$@" 2>&1; echo "[exit $?]"; }

run src/collect_flows.py 3
run src/evening_scan.py "$D"
run src/score_evenscan.py
run src/score_day.py --date "$D"
run src/track_manual.py
run src/flow_track.py
run src/ledger.py
run src/ledger.py 30000000
run src/seed_import.py
run src/playbook.py --flags
run src/collect_dart.py --from "$DART_FROM"
run src/dart_flags.py 5
run src/collect_sessions.py
run src/status_page.py
run src/db_text.py dump
run src/holdings_alert.py --watch 대원전선
run src/chart_levels.py --holdings
echo "################ DONE"
