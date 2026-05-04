# Stage 1: Build the application
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .

# --- BAKE THE KEYS INTO THE BUILD ---
ENV VITE_RECAPTCHA_SITE_KEY="6LeKiswsAAAAAMF_8fxlr1z7VCRQ0zr-oFfncfd0"
ENV VITE_GOOGLE_CHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/AAQAQC-SfqE/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=9HUfBxrsiwrTWAyTkwc6qWZ809reZvFb6tGzkJi21uQ"
ENV VITE_AUTH0_DOMAIN="dev-axwc0ui527kw0c5d.us.auth0.com"
ENV VITE_AUTH0_CLIENT_ID="sqVN3z2Er7YxXYK4FxbpOaYOqL2ju22D"
ENV VITE_AUTH0_AUDIENCE="https://code-inspector-api"

RUN npm run build

# Stage 2: Serve the application
FROM node:20-alpine
WORKDIR /app
RUN npm install -g serve

# Copy built assets
COPY --from=build /app/dist ./dist

# # Copy the entrypoint script and make sure it's executable
# COPY docker-entrypoint.sh /app/docker-entrypoint.sh
# USER root
# RUN chmod +x /app/docker-entrypoint.sh

# --- ALSO PROVIDE THEM AS RUNTIME DEFAULTS ---
ENV VITE_RECAPTCHA_SITE_KEY="6LeKiswsAAAAAMF_8fxlr1z7VCRQ0zr-oFfncfd0"
ENV VITE_GOOGLE_CHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/AAQAQC-SfqE/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=9HUfBxrsiwrTWAyTkwc6qWZ809reZvFb6tGzkJi21uQ"
ENV VITE_AUTH0_DOMAIN="dev-axwc0ui527kw0c5d.us.auth0.com"
ENV VITE_AUTH0_CLIENT_ID="sqVN3z2Er7YxXYK4FxbpOaYOqL2ju22D"
ENV VITE_AUTH0_AUDIENCE="https://code-inspector-api"

RUN addgroup -S appgroup && adduser -S appuser -G appgroup
RUN chown -R appuser:appgroup /app/dist
USER appuser

EXPOSE 3000
# ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["serve", "-s", "dist", "-l", "3000"]

