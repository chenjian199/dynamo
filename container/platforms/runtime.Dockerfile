# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
ARG ACCELERATOR
ENV DYNAMO_IMAGE_PLATFORM=${ACCELERATOR} DYN_PLATFORM=${ACCELERATOR}
ENV PATH=/opt/dynamo/venv/bin:${PATH}
ENV LD_LIBRARY_PATH=/opt/dynamo/native:${LD_LIBRARY_PATH}
COPY artifacts /opt/dynamo/artifacts
COPY audit.py /opt/dynamo/bin/audit.py
COPY entrypoint.sh /opt/dynamo/bin/entrypoint.sh
RUN python3 -m venv --system-site-packages /opt/dynamo/venv && \
    python /opt/dynamo/bin/audit.py verify /opt/dynamo/artifacts && \
    python -m pip install --no-index --no-deps --ignore-installed /opt/dynamo/artifacts/wheels/*.whl && \
    mkdir -p /opt/dynamo/native && cp -a /opt/dynamo/artifacts/native/. /opt/dynamo/native/ && \
    chmod +x /opt/dynamo/bin/entrypoint.sh && \
    python /opt/dynamo/bin/audit.py installed /opt/dynamo/artifacts
RUN if [ "$ACCELERATOR" != cuda ]; then \
        python /opt/dynamo/artifacts/vendor-patches/patch_vendor.py --platform "$ACCELERATOR" --apply > /opt/dynamo/vendor-patch.log; \
    fi && \
    if [ "$ACCELERATOR" = ascend ]; then \
        python /opt/dynamo/artifacts/vendor-patches/patch_ascend_tp.py --apply >> /opt/dynamo/vendor-patch.log; \
    fi
WORKDIR /opt/dynamo
ENTRYPOINT ["/opt/dynamo/bin/entrypoint.sh"]
CMD ["python", "-m", "dynamo.frontend", "--help"]
