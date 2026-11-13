#!/usr/bin/env bash
# The loop behind keeper.yml: see that file for why this exists.
set -uo pipefail
LOOP_MIN="${LOOP_MINUTES:-325}"
SLEEP_S="${SLEEP_SECONDS:-300}"
BASELINE_EVERY_S="${BASELINE_EVERY_SECONDS:-1800}"
end=$(( $(date +%s) + LOOP_MIN * 60 ))
last_baselines=0

# UTC slot start (HHMM) and the hemisphere scraped then; a slot can still be
# started up to 3 h late (the morning reports it reads are still the newest)
SLOTS="1300:S 1630:N 2030:all"
SLOT_GRACE_MIN=180

scrape_started() {  # scrape_started <hemisphere> <slot start, ISO UTC>
  gh run list -w scrape-resorts.yml --created ">=$2" -L 20 --json displayTitle \
    -q "[.[] | select(.displayTitle | endswith(\"[$1]\"))] | length" 2>/dev/null
}

while :; do
  now=$(date +%s)
  if [ $(( now - last_baselines )) -ge "$BASELINE_EVERY_S" ]; then
    gh workflow run baselines.yml -f league=due && last_baselines=$now && echo "$(date -u +%H:%MZ) baselines dispatched"
  fi
  today=$(date -u +%Y-%m-%d)
  for slot in $SLOTS; do
    hhmm=${slot%%:*}; hemi=${slot##*:}
    start_iso="${today}T${hhmm:0:2}:${hhmm:2:2}:00Z"
    start=$(date -u -d "$start_iso" +%s)
    [ "$now" -ge "$start" ] && [ "$now" -lt $(( start + SLOT_GRACE_MIN * 60 )) ] || continue
    n=$(scrape_started "$hemi" "$start_iso")
    if [ "${n:-}" = "0" ]; then  # an API error leaves n empty: try again next pass rather than scrape twice
      gh workflow run scrape-resorts.yml -f hemisphere="$hemi" && echo "$(date -u +%H:%MZ) scrape [$hemi] dispatched"
      sleep 20  # let the run appear in the listing before the next pass
    fi
  done
  [ $(( $(date +%s) + SLEEP_S )) -ge "$end" ] && break
  sleep "$SLEEP_S"
done
