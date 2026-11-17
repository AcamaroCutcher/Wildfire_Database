#!/bin/sh
# Link every hotspot to its fire, month by month.  Usage: link/run_link.sh 2020-01 2025-12
PSQL=${PSQL:-/Applications/Postgres.app/Contents/Versions/17/bin/psql}
DB=${WF_DB:-wildfire}
start=$1; end=$2
m="$start-01"
while [ "$(date -j -f %Y-%m-%d "$m" +%Y%m)" -le "$(date -j -f %Y-%m-%d "$end-01" +%Y%m)" ]; do
  n=$(date -j -v+1m -f %Y-%m-%d "$m" +%Y-%m-%d)
  echo "== $m"
  $PSQL -p 5432 -d "$DB" -q -At -F ' ' -c "SET TimeZone = 'UTC'; SELECT * FROM link_hotspots('$m', '$n');"
  m=$n
done
