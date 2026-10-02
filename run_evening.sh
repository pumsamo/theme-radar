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
# 2026-10-02: 당일 날짜로 일찍 돌리면 KRX 애프터마켓(16:00~20:00) 종가와 당일 수급이 빠진 채 라벨·성적이 나온다
#             (10/2 19:44에 돌려 라벨 카톡이 미확정 데이터로 나감). 20:05 이후이고 수급에 당일 행이 올라온 뒤에만 돈다.
#             그래도 돌리려면: FORCE=1 bash run_evening.sh 날짜
if [ "$D" = "$(date +%Y-%m-%d)" ] && [ "${FORCE:-0}" != "1" ]; then
  NOW_T="$(date +%H%M)"
  if (( 10#$NOW_T < 2005 )); then
    echo "!! 지금 ${NOW_T:0:2}:${NOW_T:2} — 20:05 전이라 20시 종가가 아직 없다. 애프터마켓이 끝난 뒤 다시 돌려줘."
    exit 2
  fi
  FLOW_D="$("$PY" -c "import sys; sys.path.insert(0, 'src'); import boot, collect_flows as c; print(max(c.fetch_page('005930', 1) or ['-']))" 2>/dev/null | tr -d '\r')"
  if [ "$FLOW_D" != "${D//-/}" ]; then
    echo "!! 수급(외인·기관)에 $D 행이 아직 없다(삼성전자 최신 ${FLOW_D:-조회 실패}). 조금 뒤에 다시 돌려줘."
    exit 2
  fi
fi
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
