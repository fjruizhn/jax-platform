#!/usr/bin/env bash
set -e

# Resolve the directory containing this script, regardless of cwd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_DIR")" && pwd)"

echo "Running test suites from $REPO_ROOT"
echo ""

# Run backend tests
echo "=== Backend Tests ==="
cd "$REPO_ROOT/backend"
.venv/bin/pytest -v
BACKEND_STATUS=$?

# Run frontend tests
echo ""
echo "=== Frontend Tests ==="
cd "$REPO_ROOT/frontend"
npm run test
FRONTEND_STATUS=$?

# Summary
echo ""
echo "=== Test Summary ==="
if [ $BACKEND_STATUS -eq 0 ] && [ $FRONTEND_STATUS -eq 0 ]; then
  echo "✓ All tests passed"
  exit 0
else
  echo "✗ Some tests failed"
  exit 1
fi
