# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Explicit optional CPU VP9 decoder layer over the locked CUDA runtime.
ARG RUNTIME_IMAGE
FROM ${RUNTIME_IMAGE}
USER root
COPY wheels /opt/dynamo/media-wheels
COPY wheel.json /opt/dynamo/media-wheel.json
RUN /opt/dynamo/venv/bin/python -c 'import importlib.metadata as m,json; p=json.load(open("/opt/dynamo/artifacts/profile.json")); json.dump({d.metadata["Name"]:d.version for d in m.distributions() if d.metadata["Name"].lower().replace("_","-") in p["protected_packages"]},open("/tmp/protected-before.json","w"))' && \
    /opt/dynamo/venv/bin/python -m pip install --no-index --no-deps --force-reinstall \
    --find-links /opt/dynamo/media-wheels opencv-python-headless==5.0.0.93 && \
    /opt/dynamo/venv/bin/python -c 'import cv2, re; assert re.search(r"FFMPEG:\s+YES", cv2.getBuildInformation())' && \
    /opt/dynamo/venv/bin/python -c 'import importlib.metadata as m,json; p=json.load(open("/tmp/protected-before.json")); assert all(m.version(k)==v for k,v in p.items()); print(p)'
