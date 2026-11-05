# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
ARG RUNTIME_IMAGE
FROM ${RUNTIME_IMAGE}
USER root
ARG PLATFORM
ARG PIP_INDEX_URL
WORKDIR /opt/dynamo/tools-build
COPY audit.py requirements-tools.txt requirements-planner.txt requirements-tools-constraints.txt patch-aiperf.py patch-aiperf-local-fast.py ./
COPY profile.json ./container/platforms/${PLATFORM}.json
COPY wheels ./wheels
RUN --mount=type=cache,target=/root/.cache/pip /opt/dynamo/venv/bin/python audit.py snapshot before.json && \
    /opt/dynamo/venv/bin/python audit.py constraints "$PLATFORM" protected.txt && \
    /opt/dynamo/venv/bin/python -m pip install --no-index --no-deps -c protected.txt wheels/*.whl && \
    /opt/dynamo/venv/bin/python -m pip install --cache-dir /root/.cache/pip --timeout 120 --retries 10 --find-links ./wheels --only-binary aisimulate -c protected.txt -c requirements-tools-constraints.txt -r requirements-tools.txt -r requirements-planner.txt && \
    /opt/dynamo/venv/bin/python audit.py constraints "$PLATFORM" after.txt && cmp protected.txt after.txt && \
    /opt/dynamo/venv/bin/python audit.py snapshot after.json && \
    /opt/dynamo/venv/bin/python -c "import json; from pathlib import Path; a=json.loads(Path('after.json').read_text()); b=json.loads(Path('before.json').read_text()); added=set(a['pip_check'])-set(b['pip_check']); assert not added, sorted(added)"
COPY deploy /opt/dynamo/profiler-support/deploy
COPY --chmod=755 kubectl dgd-apply-overrides /usr/local/bin/
RUN /opt/dynamo/venv/bin/python patch-aiperf.py && \
    tokenizer=$(/opt/dynamo/venv/bin/python -c "import importlib.util; print(importlib.util.find_spec('aiperf.common.tokenizer').origin)") && \
    /opt/dynamo/venv/bin/python patch-aiperf-local-fast.py --input "$tokenizer" --output /tmp/tokenizer.py && \
    cp /tmp/tokenizer.py "$tokenizer" && \
    ln -sf /bin/bash /bin/sh && mkdir -p /opt/dynamo/platform-capacity
COPY capacity-links.py ./
RUN /opt/dynamo/venv/bin/python capacity-links.py "$PLATFORM"
ENV DYN_PLATFORM=cpu TORCH_DEVICE_BACKEND_AUTOLOAD=0 PYTHONPATH=/opt/dynamo/profiler-support K3S_DATA_DIR=/tmp/dynamo-k3s
RUN /opt/dynamo/venv/bin/python -m dynamo.profiler --help && \
    /opt/dynamo/venv/bin/python -m dynamo.planner --help && \
    dgd-apply-overrides --protocol-version && kubectl version --client
WORKDIR /tmp
