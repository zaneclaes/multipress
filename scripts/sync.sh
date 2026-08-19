#!/bin/bash
SITE="$1"
MODE="${2:-local}"
EPH="/var/www/html/$SITE"
LOCAL="/var/www/shared/$SITE"
EFS="/var/multipress/$SITE"
TRG=""

if [[ $MODE = "efs" ]]; then
  TRG="${EFS}"
else
  MODE="local"
  TRG="${LOCAL}"
fi

echo "------- SYNC $SITE <> $TRG -------" >> /var/log/cron.log
# Sync the ephemeral (live) directory to the target directory.
# The live copy is authoritative: -prefer resolves conflicts toward it instead of
# skipping the path forever (skipped conflicts are how plugin dirs ended up as
# permanent mixed-version states). Transient WordPress updater state is ignored so
# a sync that runs mid-update cannot capture or resurrect a half-written plugin.
# NOTE: "Path" patterns are used because unison "Name" patterns only match a
# path's final component, so the old "Name *wp-content/cache*" never matched.
flock "/root/.unison-${MODE}-${SITE}.lock" unison -auto -batch \
  -prefer "$EPH" \
  -ignore "Name .maintenance" \
  -ignore "Path wp-content/cache" \
  -ignore "Path wp-content/upgrade" \
  -ignore "Path wp-content/upgrade-temp-backup" \
  "$EPH" "$TRG" >> "/var/log/sync-${SITE}.log" 2>&1
echo "------- SYNCED $SITE ($?) -------" >> /var/log/cron.log
