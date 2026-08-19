#!/usr/bin/env bash
SITE="${1:?usage: bak.sh <site>}"

chown www-data:www-data -R "${SITE}/"
cd "${SITE}/wp-content/" || exit 1
find . -type f -exec chmod 644 {} \;
find . -type d -exec chmod 775 {} \;
zip -r -x "*.DS_Store" -q "/var/www/html/bak/${SITE}.zip" .
