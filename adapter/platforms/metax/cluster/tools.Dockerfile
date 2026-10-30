# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Build from the repository root, with an M10 runtime from the same source commit.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
WORKDIR /opt/dynamo/tools-build
COPY container/platforms/audit.py ./audit.py
COPY container/platforms/metax.json ./container/platforms/metax.json
COPY adapter/platforms/metax/cluster/requirements-tools.txt ./requirements-tools.txt
COPY adapter/m09/requirements-planner-cuda.txt ./requirements-planner.txt
RUN /opt/dynamo/venv/bin/python audit.py snapshot before.json && \
    /opt/dynamo/venv/bin/python audit.py constraints metax protected.txt && \
    /opt/dynamo/venv/bin/python -m pip install -c protected.txt -r requirements-tools.txt -r requirements-planner.txt && \
    /opt/dynamo/venv/bin/python audit.py constraints metax after.txt && \
    cmp protected.txt after.txt && \
    /opt/dynamo/venv/bin/python audit.py snapshot after.json && \
    /opt/dynamo/venv/bin/python -c "import json; from pathlib import Path; before=json.loads(Path('before.json').read_text()); after=json.loads(Path('after.json').read_text()); added=set(after['pip_check'])-set(before['pip_check']); assert not added, sorted(added)"
COPY deploy /opt/dynamo/profiler-support/deploy
# Binaries must be built/provided for amd64 in the build context (see README).
COPY --chmod=755 artifacts/metax-cluster/kubectl artifacts/metax-cluster/dgd-apply-overrides /usr/local/bin/
COPY adapter/m08/advanced/patch-aiperf.py ./patch-aiperf.py
RUN /opt/dynamo/venv/bin/python patch-aiperf.py && \
    ln -sf /bin/bash /bin/sh && \
    mkdir -p /opt/dynamo/platform-capacity && \
    /opt/dynamo/venv/bin/python -c "from pathlib import Path; from aiconfigurator.sdk import perf_database; p=Path(perf_database.get_systems_paths()[0])/'metax_c500.yaml'; p.symlink_to('/opt/dynamo/platform-capacity/metax_c500.yaml')"
ENV DYN_PLATFORM=cpu TORCH_DEVICE_BACKEND_AUTOLOAD=0 PYTHONPATH=/opt/dynamo/profiler-support
# Capacity YAML is mounted per deployment, not guessed from a CUDA SKU.
RUN /opt/dynamo/venv/bin/python -c "from dynamo.profiler.utils.dgdr_v1beta1_types import GPUSKUType; assert GPUSKUType.MetaXC500.value == 'metax_c500'; import dynamo.planner" && \
    dgd-apply-overrides --protocol-version && kubectl version --client
WORKDIR /tmp
