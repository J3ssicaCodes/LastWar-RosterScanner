FROM nginx:stable-alpine

# Render's web services expect an HTTP server to bind to the PORT variable.
# Its default is 10000, and the official nginx entrypoint expands templates.
ENV PORT=10000

COPY index.html /usr/share/nginx/html/index.html

RUN mkdir -p /etc/nginx/templates && \
    printf '%s\n' \
      'server {' \
      '    listen ${PORT};' \
      '    server_name _;' \
      '    root /usr/share/nginx/html;' \
      '    index index.html;' \
      '    charset utf-8;' \
      '    location / {' \
      '        try_files $uri $uri/ =404;' \
      '    }' \
      '}' > /etc/nginx/templates/default.conf.template

EXPOSE 10000
