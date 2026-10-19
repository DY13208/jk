FROM node:24-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5
WORKDIR /bridge
COPY package*.json ./
RUN npm install --omit=optional --ignore-scripts --no-audit --no-fund
COPY *.mjs ./
RUN mkdir -p /state && chown node:node /state
ENV OPENCLAW_STATE_DIR=/state
USER node
CMD ["node","bridge.mjs"]
