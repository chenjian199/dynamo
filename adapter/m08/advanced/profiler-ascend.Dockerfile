# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
FROM heyi/dynamo-vllm-ascend-runtime:1.5.1-m02
USER root
ENV PIP_CACHE_DIR=/root/.cache/pip
RUN /opt/dynamo/venv/bin/python -m pip install --timeout=180 --retries=10 pandas==2.3.3 aisimulate==0.12.0 aiofiles==24.1.0 kubernetes==32.0.1 kubernetes-asyncio==32.0.0 plotext==5.3.2 plotly==6.4.0 'scipy>=1.14,<2' aiperf==0.13.0 && /opt/dynamo/venv/bin/python -c "import importlib.metadata as m; assert m.version('vllm') == '0.23.0+empty'"
COPY dgdr_v1beta1_types.py /tmp/dgdr_v1beta1_types.py
RUN /opt/dynamo/venv/bin/python -c "import importlib.util, shutil; shutil.copyfile('/tmp/dgdr_v1beta1_types.py', importlib.util.find_spec('dynamo.profiler.utils.dgdr_v1beta1_types').origin)"
COPY deploy /opt/dynamo/profiler-support/deploy
# The M08 run used read-only source overlays; bake them into rebuilt tool images.
COPY dgd_platform.py dgd_materialization.py /opt/dynamo/venv/lib/python3.12/site-packages/dynamo/profiler/utils/
COPY vllm.py /opt/dynamo/venv/lib/python3.12/site-packages/dynamo/profiler/utils/config_modifiers/vllm.py
ENV PYTHONPATH=/opt/dynamo/profiler-support
COPY kubectl /usr/local/bin/kubectl
RUN chmod 755 /usr/local/bin/kubectl
# Needed when kubectl is the K3s multicall launcher supplied by this host.
# The Operator runs the Profiler and output copier as UID/GID 1000.
RUN mkdir -p /.rancher/k3s && chown 1000:1000 /.rancher /.rancher/k3s

# The controller's output-copier script requires pipefail.
RUN ln -sf /bin/bash /bin/sh
# Capacity metadata only: no performance database or H100 alias.
RUN /opt/dynamo/venv/bin/python -c "import json; from pathlib import Path; from aiconfigurator.sdk import perf_database; p=Path(perf_database.get_systems_paths()[0]); (p/'ascend_910b3.yaml').write_text(json.dumps({'node': {'num_gpus_per_node': 8}, 'gpu': {'mem_capacity': 64 * 1024**3}}))"

COPY patch-aiperf.py /tmp/patch-aiperf.py
RUN /opt/dynamo/venv/bin/python /tmp/patch-aiperf.py
ENV TORCH_DEVICE_BACKEND_AUTOLOAD=0
