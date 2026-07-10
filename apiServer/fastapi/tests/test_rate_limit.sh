#!/bin/bash

# Configuration
API_KEY=""
API_URL="https://api-sandbox.01security.com"

echo "Testing rate limit with API Key..."
for i in {1..50}
do
  # Send request and print only the HTTP status code
  status_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/v1/scan-jobs" \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"language": "python", "code": "print(1)"}')

  echo "Request $i -> HTTP Status: $status_code"
done
