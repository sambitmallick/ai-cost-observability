#!/bin/bash
# save_ledger.sh <version>: copy the app's ledger (one JSON line per request, on its stdout) out of the pod.
# Pod logs disappear with the pod: save them right after each load test.
set -e
kubectl -n ai-cost logs deploy/ci-copilot | grep '^{' | grep "\"version\": \"$1\"" > "ledger/$1.jsonl"
echo "ledger/$1.jsonl: $(wc -l < "ledger/$1.jsonl") requests"
