#!/bin/sh
# Builds the deploy bundle of a release, as .github/workflows/release.yml did for GitHub releases.
# Usage: .onedev/bundle.sh <image> <version> <where the bundle is downloaded from> [output directory, default dist]
#   <image> is the prefix of the images: <prefix>backend, <prefix>frontend, <prefix>flow-collector, <prefix>awg-client.
# Writes <output>/harbor-<version>-deploy.tar.gz, SHA256SUMS and notes.md (the release description).
set -eu
APP=harbor; IMAGE=$1; VERSION=$2; URL=$3; OUT=${4:-dist}; TAG=v$VERSION
DIR="$OUT/$APP-$VERSION"
rm -rf "$OUT"; mkdir -p "$DIR"
cp -r docker-compose.yml .env.example .gitattributes install.sh LICENSE README.md README.ru.md \
  awg-client backend frontend flow-collector openbao clickhouse mikrotik "$DIR/"
# The images of this version (on GitHub they keep the names network-control-* for existing installations).
sed -i "s/^NC_VERSION=.*/NC_VERSION=$VERSION/" "$DIR/.env.example"
sed -i "s#ghcr.io/sbrw-evc/network-control-#$IMAGE#" "$DIR/docker-compose.yml"
tar -C "$OUT" -czf "$OUT/$APP-$VERSION-deploy.tar.gz" "$APP-$VERSION"
rm -rf "$DIR"
( cd "$OUT" && sha256sum "$APP-$VERSION-deploy.tar.gz" > SHA256SUMS )
NOTES=".github/release-notes/$TAG.md"
# shellcheck disable=SC2016 # the backticks are Markdown, not an expansion
{ [ -f "$NOTES" ] && cat "$NOTES" && echo; \
  printf '## Install\n\n```bash\ncurl -L %s | tar xz\ncd %s-%s && ./install.sh\n```\n\nThen open the address install.sh prints and go through the setup wizard.\n' \
    "$URL" "$APP" "$VERSION"; } > "$OUT/notes.md"
