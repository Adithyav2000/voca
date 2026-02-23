#!/bin/sh
set -e
cd /app
# Install deps inside container so node_modules gets Linux binaries (e.g. rollup-linux-arm64-musl)
npm ci
exec npm run dev
