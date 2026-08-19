#!/usr/bin/env bash
mkdir -p /var/www/html
mkdir -p /var/www/shared/sessions
chown -R www-data:www-data /var/www

cd /var/www/html/
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pyyaml awscli s3cmd watchdog python-magic
python /usr/local/bin/multipress.py