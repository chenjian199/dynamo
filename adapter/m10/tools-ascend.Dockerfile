# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
ARG RUNTIME_IMAGE
FROM ${RUNTIME_IMAGE}
USER root
ARG PIP_INDEX_URL
WORKDIR /opt/dynamo/tools-build
COPY audit.py requirements-tools.txt requirements-planner.txt requirements-tools-constraints.txt patch-aiperf.py patch-aiperf-local-fast.py ./
COPY wheels ./wheels
# Ascend's Triton pins scipy/psutil incompatibly with the CPU analysis stack.
# Keep the complete vendor environment intact; never expose its site-packages
# to the independent Profiler/Planner interpreter.
RUN --mount=type=cache,target=/root/.cache/pip /opt/dynamo/venv/bin/python audit.py snapshot before.json && \
    env -u PYTHONPATH /opt/dynamo/venv/bin/python -m venv /opt/dynamo/tools-venv && \
    env -u PYTHONPATH /opt/dynamo/tools-venv/bin/python -m pip install --no-index --no-deps wheels/*.whl && \
    env -u PYTHONPATH /opt/dynamo/tools-venv/bin/python -m pip install --cache-dir /root/.cache/pip --timeout 120 --retries 5 --find-links ./wheels --only-binary aisimulate -c requirements-tools-constraints.txt -r requirements-tools.txt -r requirements-planner.txt /opt/dynamo/artifacts/wheels/ai_dynamo*.whl /opt/dynamo/artifacts/wheels/kvbm*.whl /opt/dynamo/artifacts/wheels/gpu_memory_service*.whl pytest==8.4.2 pytest-asyncio==1.2.0 PyYAML blake3 pillow
RUN env -u PYTHONPATH /opt/dynamo/tools-venv/bin/python -m pip check && \
    /opt/dynamo/venv/bin/python audit.py snapshot after.json && cmp before.json after.json
COPY deploy /opt/dynamo/profiler-support/deploy
COPY --chmod=755 kubectl dgd-apply-overrides /usr/local/bin/
COPY --chmod=755 tools-entrypoint.sh /opt/dynamo/bin/tools-entrypoint.sh
ENV PATH=/opt/dynamo/tools-venv/bin:${PATH} PYTHONPATH=/opt/dynamo/profiler-support
RUN python patch-aiperf.py && \
    tokenizer=$(python -c "import importlib.util; print(importlib.util.find_spec('aiperf.common.tokenizer').origin)") && \
    python patch-aiperf-local-fast.py --input "$tokenizer" --output /tmp/tokenizer.py && \
    cp /tmp/tokenizer.py "$tokenizer" && \
    ln -sf /bin/bash /bin/sh && mkdir -p /opt/dynamo/platform-capacity
COPY capacity-links.py ./
RUN python capacity-links.py ascend
ENV DYN_PLATFORM=cpu TORCH_DEVICE_BACKEND_AUTOLOAD=0 PYTHONPATH=/opt/dynamo/profiler-support K3S_DATA_DIR=/tmp/dynamo-k3s
RUN python -m dynamo.profiler --help && python -m dynamo.planner --help && \
    dgd-apply-overrides --protocol-version && kubectl version --client
WORKDIR /tmp
ENTRYPOINT ["/opt/dynamo/bin/tools-entrypoint.sh"]
