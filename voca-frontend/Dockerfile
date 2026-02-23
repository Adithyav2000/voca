# Dev server: source mounted at runtime; entrypoint runs npm ci so node_modules volume has Linux binaries.
FROM node:20-alpine

WORKDIR /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
