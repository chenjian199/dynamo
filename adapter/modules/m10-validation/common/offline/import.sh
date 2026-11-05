#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Import on every selected node. Does not change cluster deployments or drivers.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum -c SHA256SUMS
case "${1:?Usage: bash import.sh docker|containerd|k3s}" in
    docker) importer=(docker load) ;;
    containerd) importer=(ctr --namespace k8s.io images import -) ;;
    k3s) importer=(k3s ctr --namespace k8s.io images import -) ;;
    *) echo 'Choose docker, containerd or k3s' >&2; exit 2 ;;
esac
gzip -dc images.tar.gz | "${importer[@]}"
# These are individual image archives, not the outer KAI collection tar.
# Importing optional dependencies does not install their controllers.
shopt -s nullglob
for archive in dependencies/images/*.tar; do
    "${importer[@]}" < "$archive"
done
