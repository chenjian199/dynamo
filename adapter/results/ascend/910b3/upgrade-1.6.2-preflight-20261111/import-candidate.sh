#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum -c SHA256SUMS
case "${1:?Usage: bash import-candidate.sh k3s|containerd}" in
    k3s) importer=(k3s ctr --namespace k8s.io images import --platform linux/arm64) ;;
    containerd) importer=(ctr --namespace k8s.io images import --platform linux/arm64) ;;
    *) echo 'Choose k3s or containerd' >&2; exit 2 ;;
esac
"${importer[@]}" images.tar.gz
for archive in dependencies/*.tar; do
    "${importer[@]}" "$archive"
done
echo 'Candidate images imported. No deployment or formal release promotion performed.'
