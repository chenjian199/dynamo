#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Explicitly upgrade the existing release; retain rollback information locally.
set -euo pipefail
platform=${1:?Usage: upgrade-operator.sh cuda|ascend WORKDIR IMAGE_TAG}
work=${2:?work directory}
tag=${3:?exact image tag including architecture}
cd -- "$work"
mkdir -p evidence
case "$platform" in
    cuda) arch=amd64; importer=(ctr --namespace k8s.io); helm=(helm) ;;
    ascend)
        arch=arm64
        export KUBECONFIG=${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}
        importer=(k3s ctr --namespace k8s.io)
        helm=("${HELM_BIN:?Set HELM_BIN to the verified arm64 helm binary}")
        ;;
    *) exit 2 ;;
esac
sha256sum -c operator-inputs.sha256
"${helm[@]}" get values heyi-m08 -n dynamo-m08-1029 -o json > evidence/operator-values-before.json
"${helm[@]}" history heyi-m08 -n dynamo-m08-1029 -o json > evidence/operator-history-before.json
kubectl get crd dynamographdeployments.nvidia.com dynamographdeploymentrequests.nvidia.com -o json > evidence/crds-before.json
gzip -dc "dynamo-operator-m08-$arch.tar.gz" | "${importer[@]}" images import --platform "linux/$arch" - > evidence/operator-import.log 2>&1
"${helm[@]}" upgrade heyi-m08 ./dynamo-operator-1.5.1.tgz \
    --namespace dynamo-m08-1029 -f evidence/operator-values-before.json \
    --set-string "controllerManager.manager.image.repository=heyi/dynamo-operator" \
    --set-string "controllerManager.manager.image.tag=$tag" \
    --set-string controllerManager.manager.image.pullPolicy=Never \
    --wait --timeout 10m > evidence/operator-upgrade.log 2>&1
kubectl -n dynamo-m08-1029 rollout status deployment/heyi-m08-dynamo-operator-controller-manager --timeout=180s
kubectl -n dynamo-m08-1029 get deploy,pods -o json > evidence/operator-after.json
kubectl get crd dynamographdeploymentrequests.nvidia.com -o json > evidence/dgdr-crd-after.json
"${helm[@]}" history heyi-m08 -n dynamo-m08-1029 -o json > evidence/operator-history-after.json
