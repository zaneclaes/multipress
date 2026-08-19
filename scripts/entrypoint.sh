#!/usr/bin/env bash
mkdir -p /var/www/html
mkdir -p /var/www/shared/sessions
chown -R www-data:www-data /var/www

# Use the venv baked into the image; only build one from the network if missing.
# (Rebuilding the venv on every boot added minutes to cold start and made boot
# depend on PyPI being reachable.)
if [[ ! -f /opt/venv/bin/activate ]]; then
  python -m venv /opt/venv
  source /opt/venv/bin/activate
  python -m pip install --upgrade pyyaml awscli s3cmd watchdog python-magic
else
  source /opt/venv/bin/activate
fi

python /usr/local/bin/multipress.py
