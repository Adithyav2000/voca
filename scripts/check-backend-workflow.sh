#!/usr/bin/env bash
# VOCA backend workflow check. Run from repo root after: docker compose up --build -d
# Usage: ./scripts/check-backend-workflow.sh

set -e
BASE="${1:-http://localhost:8000}"

echo "=== 1. Health ==="
curl -sS "$BASE/health"
echo ""

echo "=== 2. Ready (DB + Redis) ==="
curl -sS "$BASE/ready"
echo ""

echo "=== 3. POST /api/sessions (expect 401 without session) ==="
HTTP=$(curl -sS -o /tmp/voca_session_resp -w "%{http_code}" -X POST "$BASE/api/sessions" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"dentist cleaning tomorrow morning","location":"Brookline, MA"}')
echo "HTTP $HTTP"
if [ "$HTTP" = "401" ]; then
  echo "OK: Auth required (login in browser, then create session from UI or use session cookie)."
elif [ "$HTTP" = "200" ]; then
  echo "OK: Session created (you had a session cookie)."
  cat /tmp/voca_session_resp
else
  cat /tmp/voca_session_resp
fi
echo ""

echo "=== 4. Docs available ==="
curl -sS -o /dev/null -w "GET /docs -> %{http_code}\n" "$BASE/docs"
echo ""

echo "=== Done. For full flow: login at $BASE/api/auth/login, then POST /api/sessions with same-origin cookie, then GET /api/sessions/{id}/stream ==="
