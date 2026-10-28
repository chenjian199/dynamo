#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Build the native Operator Dockerfile, retaining its license/SBOM stages.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
arch=${1:?Usage: build-operator.sh amd64|arm64 OUTPUT_DIRECTORY}
output=${2:?Specify an output directory outside the repository}
case "$arch:$(uname -m)" in
    amd64:x86_64|arm64:aarch64) ;;
    *) echo "Run this script on a native $arch build host." >&2; exit 2 ;;
esac
docker buildx version
mkdir -p -- "$output"
output=$(cd -- "$output" && pwd)
case "$output/" in
    "$root/"*) echo "Output must be outside the repository." >&2; exit 2 ;;
esac
image="${OPERATOR_REPOSITORY:-heyi/dynamo-operator}:${OPERATOR_TAG_PREFIX:-1.5.1-m08}-$arch"
snapshot=$(mktemp -d "$output/source.XXXXXX")

# Freeze only the three build contexts, including the current uncommitted M08
# changes. No credentials, .git, reference trees or test binaries are copied.
python3 - "$root" "$snapshot" <<'PY'
import hashlib
import json
import shutil
import sys
from pathlib import Path

root, target = map(Path, sys.argv[1:])
for source, destination in (
    ("deploy/operator", "operator"),
    ("container/compliance", "compliance"),
    ("deploy/helm/charts/platform/components/operator", "operator-chart"),
):
    shutil.copytree(root / source, target / destination,
                    ignore=shutil.ignore_patterns("bin", "__pycache__", ".pytest_cache"))
files = {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(target.rglob("*")) if p.is_file()}
(target / "source-sha256.json").write_text(json.dumps(files, indent=2) + "\n")
PY

args=(--load --platform "linux/$arch" --progress plain
    --build-context "compliance=$snapshot/compliance"
    --build-context "operator-chart=$snapshot/operator-chart"
    --metadata-file "$output/build-$arch.json"
    -f "$snapshot/operator/Dockerfile" -t "$image")
# Optional Docker Hub mirror prefix, including its trailing slash. The NGC
# distroless base remains the original image specified in the Dockerfile.
if [[ -n ${DOCKER_PROXY:-} ]]; then
    args+=(--build-arg "DOCKER_PROXY=$DOCKER_PROXY")
fi
# Docker's predefined proxy arguments are not persisted as image ENV. For a
# loopback-only host proxy, opt in to BUILD_NETWORK=host as well.
if [[ -n ${BUILD_HTTP_PROXY:-} ]]; then
    export HTTP_PROXY="$BUILD_HTTP_PROXY" HTTPS_PROXY="$BUILD_HTTP_PROXY"
    args+=(--build-arg HTTP_PROXY --build-arg HTTPS_PROXY)
fi
if [[ -n ${BUILD_NETWORK:-} ]]; then
    args+=(--network "$BUILD_NETWORK")
fi
docker buildx build "${args[@]}" "$snapshot/operator" 2>&1 | tee "$output/build-$arch.log"
docker image inspect "$image" > "$output/image-$arch.json"
docker run --rm --network none "$image" --help > "$output/manager-help-$arch.txt" 2>&1
docker run --rm --network none --entrypoint /crd-apply "$image" --help > "$output/crd-help-$arch.txt" 2>&1
docker run --rm --network none --entrypoint /dgd-apply-overrides "$image" --help > "$output/overrides-help-$arch.txt" 2>&1
docker save "$image" | gzip -1 > "$output/dynamo-operator-m08-$arch.tar.gz"
(cd -- "$output" && sha256sum "dynamo-operator-m08-$arch.tar.gz" > "SHA256SUMS-$arch")
printf 'Image: %s\nArtifacts: %s\nFrozen source: %s\n' "$image" "$output" "$snapshot"
