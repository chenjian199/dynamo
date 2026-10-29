# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
FROM heyi/dynamo-vllm-cuda-runtime:1.5.1-m02
USER root
ENV PIP_CACHE_DIR=/root/.cache/pip
RUN --mount=type=cache,target=/root/.cache/pip --mount=type=bind,source=wheels,target=/wheels /opt/dynamo/venv/bin/python -m pip install --find-links=/wheels --timeout=180 --retries=10 /wheels/pandas-2.3.3-*.whl /wheels/pyarrow-26.0.0-*.whl aiofiles==24.1.0 kubernetes==32.0.1 kubernetes-asyncio==32.0.0 plotext==5.3.2 plotly==6.4.0 'scipy>=1.14,<2' aiperf==0.13.0 && /opt/dynamo/venv/bin/python -c "import importlib.metadata as m; assert m.version('vllm') == '0.28.0'"
COPY dgdr_v1beta1_types.py /tmp/dgdr_v1beta1_types.py
RUN /opt/dynamo/venv/bin/python -c "import importlib.util, shutil; shutil.copyfile('/tmp/dgdr_v1beta1_types.py', importlib.util.find_spec('dynamo.profiler.utils.dgdr_v1beta1_types').origin)"
COPY deploy /opt/dynamo/profiler-support/deploy
# The M08 run used read-only source overlays; bake them into rebuilt tool images.
COPY dgd_platform.py dgd_materialization.py /opt/dynamo/venv/lib/python3.12/site-packages/dynamo/profiler/utils/
COPY vllm.py /opt/dynamo/venv/lib/python3.12/site-packages/dynamo/profiler/utils/config_modifiers/vllm.py
ENV PYTHONPATH=/opt/dynamo/profiler-support
COPY --chmod=755 kubectl /usr/local/bin/kubectl
COPY --chmod=755 dgd-apply-overrides /usr/local/bin/dgd-apply-overrides

# The controller's output-copier script requires pipefail.
RUN ln -sf /bin/bash /bin/sh
# Capacity metadata only: no performance database or H100 alias.
RUN /opt/dynamo/venv/bin/python -c "import json; from pathlib import Path; from aiconfigurator.sdk import perf_database; p=Path(perf_database.get_systems_paths()[0]); (p/'h800_sxm.yaml').write_text(json.dumps({'node': {'num_gpus_per_node': 8}, 'gpu': {'mem_capacity': 80 * 1024**3}}))"

COPY patch-aiperf.py /tmp/patch-aiperf.py
RUN /opt/dynamo/venv/bin/python /tmp/patch-aiperf.py
