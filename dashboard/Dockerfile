FROM nginx:alpine

# Copy the static dashboard and assets to the nginx html directory
COPY index.html /usr/share/nginx/html/index.html
COPY assets /usr/share/nginx/html/assets

# Copy to a subfolder to match the requested route /login/index.html
RUN mkdir -p /usr/share/nginx/html/login && \
    cp /usr/share/nginx/html/index.html /usr/share/nginx/html/login/index.html && \
    cp -r /usr/share/nginx/html/assets /usr/share/nginx/html/login/assets

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
