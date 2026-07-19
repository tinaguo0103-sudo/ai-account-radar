FROM alpine/git:2.47.2 AS source

RUN git clone https://github.com/cooderl/wewe-rss.git /src \
    && cd /src \
    && git checkout --detach f88b023961804b986f3f1225c52d5066928df3c1 \
    && test "$(git rev-parse HEAD)" = "f88b023961804b986f3f1225c52d5066928df3c1"
COPY disable-internal-refresh-cron.patch /tmp/disable-internal-refresh-cron.patch
RUN cd /src \
    && git apply --unidiff-zero --check /tmp/disable-internal-refresh-cron.patch \
    && git apply --unidiff-zero /tmp/disable-internal-refresh-cron.patch \
    && ! grep -R "handleUpdateFeedsCron\|@Cron" apps/server/src

FROM node:20.16.0-alpine AS base
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
ENV COREPACK_ENABLE_PROJECT_SPEC=0
RUN corepack enable \
    && corepack prepare pnpm@8.15.9 --activate \
    && test "$(pnpm --version)" = "8.15.9"

FROM base AS build
COPY --from=source /src /usr/src/app
WORKDIR /usr/src/app
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile
RUN pnpm run -r build
RUN pnpm deploy --filter=server --prod /app-sqlite
RUN cd /app-sqlite \
    && rm -rf ./prisma \
    && mv prisma-sqlite prisma \
    && pnpm exec prisma generate

FROM base AS app-sqlite
COPY --from=build /app-sqlite /app
WORKDIR /app
EXPOSE 4000
ENV NODE_ENV=production
ENV HOST="0.0.0.0"
ENV SERVER_ORIGIN_URL=""
ENV MAX_REQUEST_PER_MINUTE=60
ENV AUTH_CODE=""
ENV DATABASE_URL="file:../data/wewe-rss.db"
ENV DATABASE_TYPE="sqlite"
LABEL ai-account-radar.wewe-refresh-owner="project-signed-adapter-only"
LABEL ai-account-radar.wewe-internal-cron="disabled-at-build"
LABEL ai-account-radar.wewe-upstream-commit="f88b023961804b986f3f1225c52d5066928df3c1"
RUN chmod +x ./docker-bootstrap.sh
CMD ["./docker-bootstrap.sh"]
