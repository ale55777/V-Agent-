# The EchoAI web UI, built once and used by two compose services:
#   target "site":      nginx serving the static UI on port 3000 (this PC and the LAN)
#   target "publisher": puts the current tunnel address on the Netlify copy of the UI
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/
COPY packages/agent-config/package.json packages/agent-config/
COPY packages/api-client/package.json packages/api-client/
COPY packages/shared-types/package.json packages/shared-types/
RUN npm ci --no-audit --no-fund
COPY packages packages
COPY apps/web apps/web
RUN npm run build --workspace apps/web

FROM nginx:1.27-alpine AS site
COPY deploy/compose/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/apps/web/out /usr/share/nginx/html
EXPOSE 3000

FROM node:22-alpine AS publisher
RUN npm install -g --no-audit --no-fund netlify-cli@17.38.1 && npm cache clean --force
COPY --from=build /src/apps/web/out /site
COPY deploy/compose/publish.sh /publish.sh
CMD ["sh", "/publish.sh"]
