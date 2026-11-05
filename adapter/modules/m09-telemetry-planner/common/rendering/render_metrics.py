#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Scope M09 Prometheus scraping to our test namespace and named deployments."""
import argparse
import json

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--platform", choices=["cuda", "ascend"], required=True)
a = p.parse_args()
ns = "dynamo-m08-1029"
name = "m09-metrics"
meta = {"name": name, "namespace": ns, "labels": {"heyi.io/campaign": "m09-1029"}}
if a.platform == "ascend":
    obj = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PodMonitor",
        "metadata": {
            **meta,
            "labels": {**meta["labels"], "release": "kube-prometheus-stack"},
        },
        "spec": {
            "namespaceSelector": {"matchNames": [ns]},
            "selector": {
                "matchExpressions": [
                    {
                        "key": "nvidia.com/dynamo-graph-deployment-name",
                        "operator": "In",
                        "values": [
                            "m09-sla",
                            "m09-qwen32",
                            "m09-deepseek",
                            "m09-offline",
                            "m09-offline-qwen32",
                            "m09-offline-deepseek",
                        ],
                    }
                ]
            },
            "podMetricsEndpoints": [
                {
                    "port": port,
                    "path": "/metrics",
                    "interval": "2s",
                    "honorLabels": True,
                    "relabelings": [
                        {
                            "sourceLabels": [
                                "__meta_kubernetes_pod_label_nvidia_com_dynamo_namespace"
                            ],
                            "targetLabel": "dynamo_namespace",
                        }
                    ],
                }
                for port in ["http", "system", "metrics"]
            ],
        },
    }
else:
    config = {
        "global": {"scrape_interval": "2s"},
        "scrape_configs": [
            {
                "job_name": "m09",
                "honor_labels": True,
                "kubernetes_sd_configs": [
                    {"role": "pod", "namespaces": {"names": [ns]}}
                ],
                "relabel_configs": [
                    {
                        "source_labels": [
                            "__meta_kubernetes_pod_label_nvidia_com_dynamo_graph_deployment_name"
                        ],
                        "regex": "m09-(sla|qwen32|deepseek|offline|offline-qwen32|offline-deepseek)",
                        "action": "keep",
                    },
                    {
                        "source_labels": ["__meta_kubernetes_pod_container_name"],
                        "regex": "main",
                        "action": "keep",
                    },
                    {
                        "source_labels": ["__meta_kubernetes_pod_container_port_name"],
                        "regex": "http|system|metrics",
                        "action": "keep",
                    },
                    {
                        "source_labels": ["__meta_kubernetes_pod_name"],
                        "target_label": "pod",
                    },
                    {
                        "source_labels": ["__meta_kubernetes_namespace"],
                        "target_label": "namespace",
                    },
                    {
                        "source_labels": [
                            "__meta_kubernetes_pod_label_nvidia_com_dynamo_namespace"
                        ],
                        "target_label": "dynamo_namespace",
                    },
                ],
            }
        ],
    }
    image = "m.daocloud.io/docker.io/prom/prometheus@sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996"
    items = [
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": meta},
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": meta,
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list", "watch"],
                }
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": meta,
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": name,
            },
            "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": ns}],
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": meta,
            "data": {"prometheus.yml": json.dumps(config)},
        },
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": meta,
            "spec": {
                "serviceAccountName": name,
                "nodeSelector": {"kubernetes.io/hostname": "worker06"},
                "securityContext": {"fsGroup": 65534},
                "activeDeadlineSeconds": 21600,
                "volumes": [
                    {"name": "config", "configMap": {"name": name}},
                    {"name": "data", "emptyDir": {}},
                ],
                "containers": [
                    {
                        "name": "prometheus",
                        "image": image,
                        "imagePullPolicy": "Never",
                        "args": [
                            "--config.file=/config/prometheus.yml",
                            "--storage.tsdb.path=/data",
                            "--storage.tsdb.retention.time=2h",
                        ],
                        "ports": [{"name": "http", "containerPort": 9090}],
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "256Mi"},
                            "limits": {"cpu": "2", "memory": "2Gi"},
                        },
                        "volumeMounts": [
                            {
                                "name": "config",
                                "mountPath": "/config",
                                "readOnly": True,
                            },
                            {"name": "data", "mountPath": "/data"},
                        ],
                    }
                ],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": meta,
            "spec": {
                "selector": {"heyi.io/campaign": "m09-1029", "app": "m09-metrics"},
                "ports": [{"port": 9090, "targetPort": "http"}],
            },
        },
    ]
    items[-2]["metadata"] = {**meta, "labels": {**meta["labels"], "app": "m09-metrics"}}
    obj = {"apiVersion": "v1", "kind": "List", "items": items}
print(json.dumps(obj, indent=2))
