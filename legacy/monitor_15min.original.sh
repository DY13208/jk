#!/usr/bin/env bash
# Demo 15-minute monitoring loop: each cycle runs Crawl4AI then EasySpider (sequential, never concurrent),
# ingests into SQLite and diffs against previous run. Usage: monitor_15min.sh <cycles> <first_tag_index>
# For production use cron instead:  */15 * * * * cd /workspace/crawl-test && ./monitor_15min.sh 1 auto
set -u
cd /workspace/crawl-test
CYCLES=${1:-2}; IDX=${2:-2}; INTERVAL=900
. venv/bin/activate
export DISPLAY=${DISPLAY:-:17}
for ((k=0; k<CYCLES; k++)); do
  n=$((IDX+k)); start=$(date +%s)
  echo "=== cycle run$n start $(date '+%F %T %Z') ==="
  python crawl4ai_run/run_crawl4ai.py headful run$n 2>&1 | grep '^{' > logs/crawl4ai_run$n.log; tail -1 logs/crawl4ai_run$n.log
  python3 diff_sqlite.py ingest output/runs/crawl4ai_run$n.json && python3 diff_sqlite.py diff --tool crawl4ai > logs/diff_crawl4ai_run$n.json; tail -8 logs/diff_crawl4ai_run$n.json
  sleep 20
  python3 easyspider_run/run_easyspider.py run$n 0
  python3 diff_sqlite.py ingest output/runs/easyspider_run$n.json && python3 diff_sqlite.py diff --tool easyspider > logs/diff_easyspider_run$n.json; tail -8 logs/diff_easyspider_run$n.json
  el=$(( $(date +%s) - start )); echo "=== cycle run$n done in ${el}s (budget ${INTERVAL}s) ==="
  if (( k < CYCLES-1 )); then sleep $(( INTERVAL - el > 0 ? INTERVAL - el : 0 )); fi
done
