#!/bin/sh
set -eu

redis_pid=""
worker_pid=""

cleanup() {
    if [ -n "$worker_pid" ]; then
        kill "$worker_pid" 2>/dev/null || true
    fi
    if [ -n "$redis_pid" ]; then
        kill "$redis_pid" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM

if [ -z "${REDIS_URL:-}" ]; then
    redis-server \
        --bind 127.0.0.1 \
        --port 6379 \
        --save "" \
        --appendonly no \
        --maxmemory 64mb \
        --maxmemory-policy noeviction &
    redis_pid=$!
    export REDIS_URL="redis://127.0.0.1:6379/0"

    attempts=0
    until redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 30 ]; then
            echo "Redis did not become ready." >&2
            exit 1
        fi
        sleep 1
    done
fi

rq worker papermint --url "$REDIS_URL" --worker-class rq.worker.SimpleWorker &
worker_pid=$!

uvicorn app:app \
    --host 0.0.0.0 \
    --port "${PORT:-10000}" \
    --limit-concurrency "${PAPERMINT_HTTP_CONCURRENCY:-50}" &
web_pid=$!

wait "$web_pid"
