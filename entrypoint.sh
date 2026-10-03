#!/bin/bash

# read secrets for SAMBA
. ./cormonitoring_env.$APP_ENV

[[ ! -d $SCAN_DIR ]] &&  mkdir $SCAN_DIR
chmod -R 0777 $SCAN_DIR
mount -t cifs "$SCAN_UNC/$SCAN_UNC_DIR" $SCAN_DIR -o rw,user=$SCAN_USER,password=$SCAN_PASSWORD

# just file with the timestamp
touch $SCAN_DIR/$(date +%Y%m%d%H%M).file

# upgrade DB
/usr/local/bin/alembic upgrade head

# Start WebSocket server in background (port 45762)
/usr/local/bin/uvicorn backend.routes.devices.websocket_routes:app \
    --host 0.0.0.0 \
    --port 45762 \
    --log-level info &

WEBSOCKET_PID=$!

# Start main API server on port 8000
/usr/local/bin/gunicorn main:app \
    --config /app/gunicorn.conf.py \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --log-level info \
    --error-logfile - \
    --access-logfile -

# Kill WebSocket server when main server exits
kill $WEBSOCKET_PID 2>/dev/null || true

