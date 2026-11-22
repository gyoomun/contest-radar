#!/bin/bash
# 매일 실행: DB 띄우기 → 수집 → 상세 읽기 2번. launchd(launchd/com.gyoomun.contest-radar.plist)가 부른다
cd "$(dirname "$0")"
LOG_DIR=~/Library/Logs/contest-radar
mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/$(date +%F).log" 2>&1

LOCK=/tmp/contest-radar.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%F %T') 이미 실행 중($LOCK). 건너뜀"; exit 0
fi
trap 'rmdir "$LOCK"' EXIT

START=$(date +%s)
FAILED=()
step() {
  echo "=== $(date '+%F %T') $*"
  "$@" || { echo "!!! 실패(exit $?): $*"; FAILED+=("$*"); }
}
notify() { osascript -e "display notification \"$1\" with title \"contest-radar\""; }

# Docker Desktop 이 꺼져 있으면 켜고 최대 3분 기다린다
docker info >/dev/null 2>&1 || open -ga Docker
for _ in $(seq 36); do docker info >/dev/null 2>&1 && break; sleep 5; done
step docker compose up -d
for _ in $(seq 24); do docker exec research-postgres pg_isready -q -U research && break; sleep 5; done

step uv run collect.py
step uv run detail.py
step uv run detail.py   # 네트워크 탓에 실패한 것을 한 번 더

# 이번 실행에서 실패했거나 0건인 사이트
BAD=$(docker exec research-postgres psql -U research -d research -tAc "
  SELECT string_agg(s.name, ', ' ORDER BY s.id) FROM sources s
  WHERE s.enabled AND NOT EXISTS (
    SELECT 1 FROM runs r WHERE r.source_id = s.id AND r.ran_at >= to_timestamp($START)
      AND r.error IS NULL AND r.found > 0)")
MSG=""
[ -n "$BAD" ] && MSG="실패·0건: $BAD"
[ ${#FAILED[@]} -gt 0 ] && MSG="$MSG 단계 실패: ${FAILED[*]}"
if [ -n "$MSG" ]; then
  echo "!!! 알림: $MSG"; notify "$MSG"
fi
echo "=== $(date '+%F %T') 끝"
