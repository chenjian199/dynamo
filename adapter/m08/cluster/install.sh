#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Run only on the designated cluster administration host after inventory review.
set -euo pipefail
platform=${1:?Usage: install.sh cuda|ascend WORK_DIRECTORY}
work=${2:?Specify the directory containing the verified offline bundle}
cd -- "$work"
case "$platform" in
    cuda) arch=amd64; ctr_cmd=(ctr --namespace k8s.io); helm_cmd=(helm) ;;
    ascend)
        arch=arm64
        export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
        ctr_cmd=(k3s ctr --namespace k8s.io)
        sha256sum -c helm-v3.17.3-linux-arm64.tar.gz.sha256sum
        tar -xzf helm-v3.17.3-linux-arm64.tar.gz
        helm_cmd=("$PWD/linux-arm64/helm")
        ;;
    *) echo 'Supported platforms: cuda, ascend' >&2; exit 2 ;;
esac
sha256sum -c dynamo-m08-operator-bundle.tar.sha256
tar -xf dynamo-m08-operator-bundle.tar
mkdir -p source evidence
sha256sum -c m08-source.tar.gz.sha256
tar -xzf m08-source.tar.gz -C source
(
    cd dynamo-m08-operator-bundle
    sha256sum -c SHA256SUMS > ../evidence/bundle-checksums.txt
)
bundle="$PWD/dynamo-m08-operator-bundle"
gzip -dc "$bundle/dynamo-operator-m08-$arch.tar.gz" |
    "${ctr_cmd[@]}" images import --platform "linux/$arch" - > evidence/image-import.log 2>&1
"${helm_cmd[@]}" list -A -o json > evidence/helm-before.json
kubectl get nodes -o json > evidence/nodes.json
"${helm_cmd[@]}" upgrade --install heyi-m08 "$bundle/dynamo-operator-1.5.1.tgz" \
    --namespace dynamo-m08-1029 --create-namespace \
    -f "$bundle/operator-values-$arch.yaml" \
    -f "$platform-operator-overlay.json" --wait --timeout 10m \
    > evidence/helm-install.log 2>&1
kubectl -n dynamo-m08-1029 get deploy,pods,svc -o wide
