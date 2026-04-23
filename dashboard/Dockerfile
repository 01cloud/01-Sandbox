FROM nginx:alpine

# Copy the static dashboard to the nginx html directory
COPY index.html /usr/share/nginx/html/index.html

# Copy to a subfolder to match the requested route /login/index.html
RUN mkdir -p /usr/share/nginx/html/login && \
    cp /usr/share/nginx/html/index.html /usr/share/nginx/html/login/index.html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
