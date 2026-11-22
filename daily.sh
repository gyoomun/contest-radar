#!/bin/bash
# 매일 실행: DB 띄우기 → 수집 → 상세 읽기 2번.
# launchd(launchd/com.gyoomun.contest-radar.plist)가 1시간마다·로그인 때 부르면, 08:30 이 지났고 오늘 아직 안 돌았을 때만 돈다
# (맥이 꺼져 있던 날도 켜진 뒤 돌게. 일지 자동 정리와 같은 시각). 뷰어의 [지금 수집]은 --force 로 바로 돌린다
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
LOG_DIR=~/Library/Logs/contest-radar
mkdir -p "$LOG_DIR"
DONE="$LOG_DIR/last-done"   # 마지막으로 끝까지 돈 날짜
if [ "$1" != "--force" ]; then
  [ "$(date +%H%M)" -lt 0830 ] && exit 0
  [ "$(cat "$DONE" 2>/dev/null)" = "$(date +%F)" ] && exit 0
fi
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

# 이번 실행에서 실패한 사이트. 0건은 새 글이 없는 평범한 날에도 나와서 알리지 않는다(뷰어가 7일째 새 글 없음을 띄운다)
BAD=$(docker exec research-postgres psql -U research -d research -tAc "
  SELECT string_agg(DISTINCT s.name, ', ') FROM runs r JOIN sources s ON s.id = r.source_id
  WHERE r.ran_at >= to_timestamp($START) AND r.error IS NOT NULL")
MSG=""
[ -n "$BAD" ] && MSG="수집 실패: $BAD"
[ ${#FAILED[@]} -gt 0 ] && MSG="$MSG 단계 실패: ${FAILED[*]}"
if [ -n "$MSG" ]; then
  echo "!!! 알림: $MSG"; notify "$MSG"
fi
date +%F > "$DONE"
echo "=== $(date '+%F %T') 끝"
