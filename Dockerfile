FROM wordpress:php8.2-fpm-alpine
MAINTAINER Zane Claes <zane@technicallywizardry.com>

# --------------------------------------------------------------------------------------------------
# DEPENDENCIES + WordPress support
# --------------------------------------------------------------------------------------------------

RUN mkdir -p /usr/src/app

RUN apk --update add tar build-base gcompat linux-headers pcre-dev zlib-dev \
    gettext openssl-dev git cmake curl curl-dev msgpack-c-dev libgcc libxml2-dev \
    python3 zip unzip inotify-tools \
    freetype libpng libjpeg-turbo freetype-dev libpng-dev libjpeg-turbo-dev libwebp-dev && \
    python3 -m ensurepip && \
    rm -r /usr/lib/python*/ensurepip && \
    pip3 install --upgrade pip setuptools && \
    ln -sf pip3 /usr/bin/pip && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    pip3 install --upgrade awscli s3cmd watchdog python-magic && \
    rm /var/cache/apk/*

RUN docker-php-ext-configure gd \
    --enable-gd \
    --with-freetype=/usr/include/ \
    --with-webp=/usr/include/ \
#    --with-png-dir=/usr/include/ \
    --with-jpeg=/usr/include/ && \
  NPROC=$(grep -c ^processor /proc/cpuinfo 2>/dev/null || 1) && \
  docker-php-ext-install -j${NPROC} gd && \
  docker-php-ext-install xml && \
  docker-php-ext-install soap

# --------------------------------------------------------------------------------------------------
# TRACING
# --------------------------------------------------------------------------------------------------

# https://github.com/opentracing-contrib/nginx-opentracing/issues/72
ENV NGINX_VERSION 1.25.2
ENV NGINX_OPENTRACING_CPP_VERSION="v1.6.0"
ENV DATADOG_OPENTRACING_VERSION="v1.3.7"
ENV DATADOG_NGINX_VERSION="v1.0.3"
ENV DATADOG_PHP_VERSION="0.91.2"

ENV MAKEFLAGS="-j4"

RUN cd /usr/src/app && \
    git clone -b $NGINX_OPENTRACING_CPP_VERSION https://github.com/opentracing/opentracing-cpp.git && \
    cd opentracing-cpp && \
    mkdir .build && cd .build && \
    cmake -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF .. && \
    make && make install
#RUN cd /usr/src/app && \
#    git clone -b $DATADOG_OPENTRACING_VERSION https://github.com/DataDog/dd-opentracing-cpp && \
#    cd dd-opentracing-cpp && \
#    sed -i $'s/--prefix/--without-libidn2 --prefix/' scripts/install_dependencies.sh && \
#    scripts/install_dependencies.sh && \
#    mkdir .build && cd .build && \
#    cmake .. && \
#    make && make install
#RUN export DD_TRACE_PHP_BIN=$(which php-fpm) && \
#    cd /usr/src/app && \
#    curl -LO https://github.com/DataDog/dd-trace-php/releases/latest/download/datadog-setup.php && \
#    php datadog-setup.php --php-bin=all --enable-appsec --enable-profiling && \
#    rm -rf datadog-setup.php
RUN cd /usr/src/app && \
    wget https://github.com/DataDog/nginx-datadog/releases/download/${DATADOG_NGINX_VERSION}/nginx_${NGINX_VERSION}-alpine-ngx_http_datadog_module.so.tgz && \
    tar -xvf nginx_*_datadog_module.so.tgz && \
    rm nginx_*_datadog_module.so.tgz

# --------------------------------------------------------------------------------------------------
# NGINX installation
# FROM: https://github.com/nginxinc/docker-nginx/blob/master/mainline/alpine/Dockerfile
# --------------------------------------------------------------------------------------------------

RUN cd /usr/src/app && \
    git clone https://github.com/opentracing-contrib/nginx-opentracing.git && \
    wget http://labs.frickle.com/files/ngx_cache_purge-2.3.tar.gz && \
    tar -xvf ngx_cache_purge-*.tar.gz && \
    rm ngx_cache_purge-*.tar.gz && \
    mv ngx_cache_purge-* /usr/src/app/ngx_cache_purge

RUN GPG_KEYS=573BFD6B3D8FBC641079A6ABABF5BD827BD9BF62 \
    && CONFIG="\
        --prefix=/etc/nginx \
        --sbin-path=/usr/sbin/nginx \
        --modules-path=/usr/lib/nginx/modules \
        --conf-path=/etc/nginx/nginx.conf \
        --error-log-path=/var/log/nginx/error.log \
        --http-log-path=/var/log/nginx/access.log \
        --pid-path=/var/run/nginx.pid \
        --lock-path=/var/run/nginx.lock \
        --http-client-body-temp-path=/var/cache/nginx/client_temp \
        --http-proxy-temp-path=/var/cache/nginx/proxy_temp \
        --http-fastcgi-temp-path=/var/cache/nginx/fastcgi_temp \
        --http-uwsgi-temp-path=/var/cache/nginx/uwsgi_temp \
        --http-scgi-temp-path=/var/cache/nginx/scgi_temp \
        --user=nginx \
        --group=nginx \
#        --with-ngx_cache_purge \
        --with-http_ssl_module \
        --with-http_realip_module \
        --with-http_addition_module \
        --with-http_sub_module \
        --with-http_dav_module \
        --with-http_flv_module \
        --with-http_mp4_module \
        --with-http_gunzip_module \
        --with-http_gzip_static_module \
        --with-http_random_index_module \
        --with-http_secure_link_module \
        --with-http_stub_status_module \
        --with-http_auth_request_module \
        --with-http_xslt_module=dynamic \
        --with-http_image_filter_module=dynamic \
        --with-http_geoip_module=dynamic \
        --with-threads \
        --with-stream \
        --with-stream_ssl_module \
        --with-stream_ssl_preread_module \
        --with-stream_realip_module \
        --with-stream_geoip_module=dynamic \
        --with-http_slice_module \
        --with-mail \
        --with-mail_ssl_module \
        --with-compat \
        --with-file-aio \
        --with-http_v2_module \
        --add-module=/usr/src/app/ngx_cache_purge \
        --add-dynamic-module=/usr/src/app/nginx-opentracing/opentracing \
    " \
    && addgroup -S nginx \
    && adduser -D -S -h /var/cache/nginx -s /sbin/nologin -G nginx nginx \
    && apk add --no-cache --virtual .build-deps \
        gcc \
        libc-dev \
        make \
        openssl-dev \
        pcre-dev \
        zlib-dev \
        linux-headers \
        curl \
        gnupg \
        libxslt-dev \
        gd-dev \
        geoip-dev \
    && curl -fSL https://nginx.org/download/nginx-$NGINX_VERSION.tar.gz -o nginx.tar.gz \
    && curl -fSL https://nginx.org/download/nginx-$NGINX_VERSION.tar.gz.asc  -o nginx.tar.gz.asc \
    && export GNUPGHOME="$(mktemp -d)" \
    #&& found=''; \
    #for server in \
    #    keyserver.ubuntu.com \
    #    ha.pool.sks-keyservers.net \
    #    hkp://keyserver.ubuntu.com:80 \
    #    hkp://p80.pool.sks-keyservers.net:80 \
    #    pgp.mit.edu \
    #; do \
    #    echo "Fetching GPG key $GPG_KEYS from $server"; \
    #    gpg --keyserver "$server" --keyserver-options timeout=10 --recv-keys "$GPG_KEYS" && found=yes && break; \
    #done; \
    #test -z "$found" && echo >&2 "error: failed to fetch GPG key $GPG_KEYS" && exit 1; \
    && curl https://nginx.org/keys/thresh.key | gpg --import && \
    gpg --batch --verify nginx.tar.gz.asc nginx.tar.gz \
    && rm -rf "$GNUPGHOME" nginx.tar.gz.asc \
    && mkdir -p /usr/src \
    && tar -zxC /usr/src -f nginx.tar.gz \
    && rm nginx.tar.gz \
    && cd /usr/src/nginx-$NGINX_VERSION \
    && ./configure $CONFIG --with-debug \
    && make -j$(getconf _NPROCESSORS_ONLN) \
    && mv objs/nginx objs/nginx-debug \
    && mv objs/ngx_http_xslt_filter_module.so objs/ngx_http_xslt_filter_module-debug.so \
    && mv objs/ngx_http_image_filter_module.so objs/ngx_http_image_filter_module-debug.so \
    && mv objs/ngx_http_geoip_module.so objs/ngx_http_geoip_module-debug.so \
    && mv objs/ngx_stream_geoip_module.so objs/ngx_stream_geoip_module-debug.so \
    && ./configure $CONFIG \
    && make -j$(getconf _NPROCESSORS_ONLN) \
    && make install \
    && rm -rf /etc/nginx/html/ \
    && mkdir /etc/nginx/conf.d/ \
    && mkdir -p /usr/share/nginx/html/ \
    && install -m644 html/index.html /usr/share/nginx/html/ \
    && install -m644 html/50x.html /usr/share/nginx/html/ \
    && install -m755 objs/nginx-debug /usr/sbin/nginx-debug \
    && install -m755 objs/ngx_http_xslt_filter_module-debug.so /usr/lib/nginx/modules/ngx_http_xslt_filter_module-debug.so \
    && install -m755 objs/ngx_http_image_filter_module-debug.so /usr/lib/nginx/modules/ngx_http_image_filter_module-debug.so \
    && install -m755 objs/ngx_http_geoip_module-debug.so /usr/lib/nginx/modules/ngx_http_geoip_module-debug.so \
    && install -m755 objs/ngx_stream_geoip_module-debug.so /usr/lib/nginx/modules/ngx_stream_geoip_module-debug.so \
    && ln -s ../../usr/lib/nginx/modules /etc/nginx/modules \
    && strip /usr/sbin/nginx* \
    && strip /usr/lib/nginx/modules/*.so \
    && rm -rf /usr/src/nginx-$NGINX_VERSION \
    \
    # Bring in gettext so we can get `envsubst`, then throw
    # the rest away. To do this, we need to install `gettext`
    # then move `envsubst` out of the way so `gettext` can
    # be deleted completely, then move `envsubst` back.
    && apk add --no-cache --virtual .gettext gettext \
    && mv /usr/bin/envsubst /tmp/ \
    \
    && runDeps="$( \
        scanelf --needed --nobanner --format '%n#p' /usr/sbin/nginx /usr/lib/nginx/modules/*.so /tmp/envsubst \
            | tr ',' '\n' \
            | sort -u \
            | awk 'system("[ -e /usr/local/lib/" $1 " ]") == 0 { next } { print "so:" $1 }' \
    )" \
    && apk add --no-cache --virtual .nginx-rundeps $runDeps \
    && apk del .build-deps \
    && apk del .gettext \
    && mv /tmp/envsubst /usr/local/bin/ \
    && apk add --no-cache tzdata

# --------------------------------------------------------------------------------------------------

RUN rm -rf /var/cache/apk/*

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
RUN mkdir -p /var/www/sessions

COPY multipress.py /usr/local/bin/
RUN chmod -R +x /usr/local/bin/multipress.py

ENTRYPOINT /usr/local/bin/multipress.py
