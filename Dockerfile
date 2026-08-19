FROM inzania/multipress:base
MAINTAINER Zane Claes <zane@technicallywizardry.com>

# --------------------------------------------------------------------------------------------------
# APP
# --------------------------------------------------------------------------------------------------

COPY conf-nginx/nginx.conf /etc/nginx/nginx.conf
# COPY conf-nginx/fastcgi_params.conf /etc/nginx/fastcgi_params
COPY conf-nginx/default.conf /etc/nginx-default.conf
COPY conf-nginx/status.conf /etc/nginx/conf.d/status.conf

RUN rm -rf /usr/local/etc/php-fpm.d/*
RUN mv /usr/local/bin/docker-entrypoint.sh /usr/local/bin/wp-entrypoint.sh
RUN sed -i '$d' /usr/local/bin/wp-entrypoint.sh

COPY conf-php/ /usr/local/etc/php/conf.d
COPY templates/ /usr/local/etc/templates
COPY conf-php-fpm/ /usr/local/etc/php-fpm.d
COPY dd-config.json /etc/

COPY scripts /usr/local/bin/
RUN chmod +x /usr/local/bin/multipress.py
RUN chmod +x /usr/local/bin/sync.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT /usr/local/bin/entrypoint.sh
