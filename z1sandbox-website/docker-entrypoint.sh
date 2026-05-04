#!/bin/sh
# This script runs when the container starts (Runtime)
# It captures your ECS / Kubernetes environment variables and makes them available to the browser.

echo "window._env_ = {" > /app/dist/env-config.js
echo "  VITE_RECAPTCHA_SITE_KEY: \"$VITE_RECAPTCHA_SITE_KEY\"," >> /app/dist/env-config.js
echo "  VITE_GOOGLE_CHAT_WEBHOOK_URL: \"$VITE_GOOGLE_CHAT_WEBHOOK_URL\"," >> /app/dist/env-config.js
echo "  VITE_AUTH0_DOMAIN: \"$VITE_AUTH0_DOMAIN\"," >> /app/dist/env-config.js
echo "  VITE_AUTH0_CLIENT_ID: \"$VITE_AUTH0_CLIENT_ID\"," >> /app/dist/env-config.js
echo "  VITE_AUTH0_AUDIENCE: \"$VITE_AUTH0_AUDIENCE\"," >> /app/dist/env-config.js
echo "};" >> /app/dist/env-config.js

echo "[Runtime] Generated /app/dist/env-config.js from environment variables."

# Execute the actual command (serve)
exec "$@"
