#!/usr/bin/env bash
$SITE="juliry"

chown www-data:www-data -R "${SITE}/"
cd "${SITE}/wp-content/"
find . -type f -exec chmod 644 {} \;
find . -type d -exec chmod -R 775 {} \;
zip -r -x "*.DS_Store" -q "/var/www/html/bak/${SITE}.zip" .

Fela Kuti