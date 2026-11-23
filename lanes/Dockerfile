# syntax=docker/dockerfile:1.7
# The lane image (ADR-0059 decision 1): the orchestrator image at the same commit, plus what the
# scheduled lanes need beyond it. `Release image` builds it FROM the orchestrator image it has just
# pushed, by digest, so both images hold the same lane code. Build context: the repository root.
#
# Every tool is pinned by version. Each downloaded binary is checked against its sha256 digest by
# `ADD --checksum`, which fails the build on a mismatch. The digests are the ones each project
# publishes for the release asset (GitHub's asset digest, or Node's SHASUMS256.txt).
# One file isn't a tool release: infraops-mcp-server's change-window bundle, pinned by commit and
# checked against the digest of the file at that commit (ADR-0059 Amendment 18).
# The Debian packages are pinned by version; apt checks them against the archive's signed index.
ARG ORCHESTRATOR_IMAGE

FROM ${ORCHESTRATOR_IMAGE} AS tools
USER root
WORKDIR /tools
ARG SUPERCRONIC_VERSION=v0.2.51
ARG GH_VERSION=2.95.0
ARG UV_VERSION=0.10.9
ARG NODE_VERSION=v26.4.0
ARG BWS_VERSION=2.0.0
# The change-window lane's program (ADR-0059 Amendment 18): one esbuild file, every dependency
# bundled, so no `npm ci`. The digest attests the bytes; that they are this commit's own build is
# infraops' `Build` job, which a bump confirms (`docs/operations/deploy.md`, "Bump change-window's
# infraops pin"). Bump both lines together, never to a branch.
ARG INFRAOPS_COMMIT=8dc4d43e309499e7420848177e1adabc8479f418
ADD --checksum=sha256:cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123 \
    https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 \
    supercronic
ADD --checksum=sha256:25d1e4729e8808c9ed3d613e96ebd3f3e44446f2d368c89d878a71a36ddb3d8c \
    https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz \
    gh.tar.gz
ADD --checksum=sha256:20d79708222611fa540b5c9ed84f352bcd3937740e51aacc0f8b15b271c57594 \
    https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz \
    uv.tar.gz
ADD --checksum=sha256:f221dab30d0e9d544332f06fbee2c62186c97d864d5cac8482dba34f1e38b8dc \
    https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.gz \
    node.tar.gz
ADD --checksum=sha256:a8340ce01da609200441f2eca0e591173e124f012c88a16afda574279c052013 \
    https://github.com/bitwarden/sdk-sm/releases/download/bws-v${BWS_VERSION}/bws-x86_64-unknown-linux-gnu-${BWS_VERSION}.zip \
    bws.zip
ADD --checksum=sha256:cb94a415ec9395bfb4ca4431fd126dc947ad5ef046aca1d3efb1bb5d91b1f228 \
    https://raw.githubusercontent.com/AlobarQuest/infraops-mcp-server/${INFRAOPS_COMMIT}/dist/bundle/change-mgr-cli.js \
    change-mgr-cli.js
RUN mkdir -p /out/bin /out/node /out/infraops \
    && install -m 0755 supercronic /out/bin/supercronic \
    && tar -xzf gh.tar.gz --strip-components=2 -C /out/bin "gh_${GH_VERSION}_linux_amd64/bin/gh" \
    && tar -xzf uv.tar.gz --strip-components=1 -C /out/bin \
      uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx \
    && tar -xzf node.tar.gz --strip-components=1 -C /out/node \
    && /usr/local/bin/python -m zipfile -e bws.zip /out/bin \
    && chmod 0755 /out/bin/* \
    && install -m 0444 change-mgr-cli.js /out/infraops/change-mgr-cli.js

FROM ${ORCHESTRATOR_IMAGE}
USER root
ARG GIT_VERSION=1:2.47.3-0+deb13u1
ARG CURL_VERSION=8.14.1-2+deb13u7
ARG CA_CERTIFICATES_VERSION=20250419+deb13u1
# Node's linux-x64 build links libatomic, which the slim base doesn't carry.
ARG LIBATOMIC_VERSION=14.2.0-19
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      "git=${GIT_VERSION}" "curl=${CURL_VERSION}" "ca-certificates=${CA_CERTIFICATES_VERSION}" \
      "libatomic1=${LIBATOMIC_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tools /out/bin/ /usr/local/bin/
COPY --from=tools /out/node /usr/local/lib/node
# The bundle keeps its name: it runs `main()` only when its path ends with `change-mgr-cli.js`, and
# under any other name exits 0 having done nothing. Root-owned and read-only, so a lane process
# can't rewrite it; `run-change-window.sh` runs it as `$REPO_ROOT/infraops/change-mgr-cli.js`.
COPY --from=tools /out/infraops/ /app/infraops/
RUN for tool in node npm npx; do ln -s "../lib/node/bin/$tool" "/usr/local/bin/$tool"; done

# The launchers, the six helpers they source, the two programs they and the scheduler run, and the
# secrets broker's program, which `lanes/secrets-broker/compose.yaml` runs in place of the
# entrypoint. Root-owned, so a lane process can't rewrite its own launcher. Each launcher sets
# REPO_ROOT to its directory's parent, so here REPO_ROOT is /app and `$REPO_ROOT/.venv/bin/<lane>`
# is the orchestrator image's own entry point.
COPY scripts/run-*.sh scripts/sds-*.sh scripts/sds_scope.py scripts/lane_run.py \
  scripts/secrets_broker.py /app/scripts/
COPY lanes/lane-run /usr/local/bin/lane-run
# Each lane's tracked crontab and compose, and the broker's allowlist and compose. The entrypoint
# reads only `/app/lanes/<lane>/crontab`.
COPY lanes/ /app/lanes/

# One state directory per stateful lane (decision 1, "Lane state"), owned by the user every lane
# process runs as. A new named volume mounted on one takes its owner.
RUN for lane in bump-driver bump-proposer estate-landing execution-watch inert-landing \
      pin-watcher revision-watcher rotation-decomposer rotation-proposer unit-verifier \
      work-carrier; do \
      install -d -m 0700 -o orchestrator -g orchestrator "/var/lib/sds/$lane"; \
    done \
    && for crontab in /app/lanes/*/crontab; do supercronic -test "$crontab" || exit 1; done

# The work carrier's packages clone (ADR-0059 decision 1). `package_sources` looks for intent-packages
# beside the orchestrator's own checkout (`_intent_packages_src`: `/intent-packages/src` for `/app`),
# and only root can create `/intent-packages`. So a root-owned link points into a directory the lane
# user owns, where `run-work-carrier.sh` clones each pass and which it discards. Not a volume: the
# clone is rebuilt every pass.
RUN install -d -m 0700 -o orchestrator -g orchestrator /var/tmp/sds-work-carrier \
    && ln -s /var/tmp/sds-work-carrier/intent-packages /intent-packages

ENV TZ=America/New_York
USER orchestrator
# The inherited HEALTHCHECK requests /health/live on port 8000, which no lane serves. A lane's
# health signal is its Healthchecks check (verify report item 12).
HEALTHCHECK NONE
ENTRYPOINT ["/app/lanes/entrypoint.sh"]
