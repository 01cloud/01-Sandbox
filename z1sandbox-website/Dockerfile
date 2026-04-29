# Stage 1: Build the application
FROM node:20-alpine AS build

WORKDIR /app

# Copy package files and install dependencies
COPY package*.json ./
RUN npm install

# Copy the rest of the application code
COPY . .

# ── Build-time Vite environment variables ─────────────────────────────────
ARG VITE_RECAPTCHA_SITE_KEY
ENV VITE_RECAPTCHA_SITE_KEY=$VITE_RECAPTCHA_SITE_KEY

ARG VITE_GOOGLE_CHAT_WEBHOOK_URL
ENV VITE_GOOGLE_CHAT_WEBHOOK_URL=$VITE_GOOGLE_CHAT_WEBHOOK_URL

# Auth0 configuration (baked into the JS bundle at build time)
ARG VITE_AUTH0_DOMAIN
ENV VITE_AUTH0_DOMAIN=$VITE_AUTH0_DOMAIN

ARG VITE_AUTH0_CLIENT_ID
ENV VITE_AUTH0_CLIENT_ID=$VITE_AUTH0_CLIENT_ID

ARG VITE_AUTH0_AUDIENCE
ENV VITE_AUTH0_AUDIENCE=$VITE_AUTH0_AUDIENCE
# ──────────────────────────────────────────────────────────────────────────

# Build the application
RUN npm run build

# Stage 2: Serve the application using Nginx
FROM nginx:stable-alpine

# Copy the build output from the previous stage
# NOTE: In Kubernetes the nginx.conf is mounted via a ConfigMap (see Helm chart).
#       The default conf below is only used outside of k8s (e.g. local Docker run).
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# Expose port 80
EXPOSE 80

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]
