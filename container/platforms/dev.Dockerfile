# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
ARG RUNTIME_IMAGE
FROM ${RUNTIME_IMAGE}
USER root
ENV DYNAMO_IMAGE_TARGET=dev
ENV CARGO_HOME=/opt/dynamo/cargo RUSTUP_HOME=/opt/dynamo/rustup
ENV PATH=/opt/dynamo/cargo/bin:${PATH}
ENV CARGO_TARGET_DIR=/build-cache/target
ARG RUST_MIRROR
COPY dev-setup.sh dev-check.py /opt/dynamo/bin/
COPY dev.txt rust-toolchain.toml /opt/dynamo/dev/
RUN bash /opt/dynamo/bin/dev-setup.sh
# Hatchling's optional PEP 660 backend is required with --no-build-isolation.
RUN python -m pip install -c /opt/dynamo/dev/runtime-constraints.txt editables==0.5 \
        --report /opt/dynamo/dev/editable-install.json && \
    python /opt/dynamo/bin/dev-check.py && \
    python -m pip freeze --all > /opt/dynamo/dev/python.txt
# Setuptools' editable finder otherwise loses to the inherited system GMS package.
ENV PYTHONPATH=/workspace/dynamo/lib:${PYTHONPATH}
COPY dev-editable.sh /opt/dynamo/bin/dev-editable.sh
RUN chmod +x /opt/dynamo/bin/dev-editable.sh
WORKDIR /workspace/dynamo
CMD ["bash"]
