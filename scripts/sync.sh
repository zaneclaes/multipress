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
# Sync the ephemeral directory to the target directory
flock "/root/.unison-${MODE}-${SITE}.lock" unison -auto -batch -ignore "Name *wp-content/cache*" $EPH $TRG >> "/var/log/sync-${SITE}.log"
echo "------- SYNCED $SITE -------" >> /var/log/cron.log
