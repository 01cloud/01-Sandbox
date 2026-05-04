# Stage 1: Build the application
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
# Vite automatically uses the .env file found in the root during build
RUN npm run build

# Stage 2: Serve the application
FROM node:20-alpine
WORKDIR /app
RUN npm install -g serve

# Copy built assets
COPY --from=build /app/dist ./dist

# Copy .env and the entrypoint script
COPY .env /app/.env
# COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# USER root
# RUN chmod +x /app/docker-entrypoint.sh

RUN addgroup -S appgroup && adduser -S appuser -G appgroup
RUN chown -R appuser:appgroup /app/dist /app/.env
USER appuser

EXPOSE 3000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["serve", "-s", "dist", "-l", "3000"]

