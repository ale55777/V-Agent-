#!/bin/sh
# Watches the tunnel container and, whenever its address changes, points the Netlify copy of
# the UI at it by redeploying with a new backend.js. Netlify uploads only changed files, so
# each update is one small file. Without NETLIFY_AUTH_TOKEN it only prints the long link.
set -u
SITE="${ECHOAI_PUBLIC_SITE:-https://echoai-ale55777.netlify.app}"
METRICS="http://tunnel:20241/quicktunnel"
current=""

while true; do
    host=$(wget -qO- "$METRICS" 2>/dev/null | sed -n 's/.*"hostname":"\([^"]*\)".*/\1/p')
    if [ -n "$host" ] && [ "$host" != "$current" ]; then
        url="https://$host"
        # A new tunnel hostname takes a few seconds to resolve worldwide.
        tries=0
        until wget -q -O /dev/null "$url/api/v1/health" 2>/dev/null || [ $tries -ge 30 ]; do
            tries=$((tries + 1)); sleep 2
        done
        echo "Tunnel is up: $url"
        if [ -n "${NETLIFY_AUTH_TOKEN:-}" ] && [ -n "${NETLIFY_SITE_ID:-}" ]; then
            rm -rf /tmp/site && cp -r /site /tmp/site
            printf 'window.__ECHOAI_API__ = "%s";\n' "$url" > /tmp/site/backend.js
            # netlify-cli 17 deploys the folder as is (no build step) and has no --no-build.
            if netlify deploy --prod --dir /tmp/site --site "$NETLIFY_SITE_ID" \
                --message "Tunnel address" > /tmp/deploy.log 2>&1; then
                echo "Netlify now points at this PC. Open: $SITE"
                current="$host"
            else
                echo "Netlify update failed (will retry):"; tail -5 /tmp/deploy.log
            fi
        else
            echo "NETLIFY_AUTH_TOKEN is not set, so Netlify was not updated. Open: $SITE/?api=$url"
            current="$host"
        fi
    fi
    sleep 15
done
