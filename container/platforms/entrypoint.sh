#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
if [[ ${DYNAMO_IMAGE_PLATFORM} == ascend ]]; then
    set +u
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
    set -u
fi
export PATH=/opt/dynamo/venv/bin:$PATH
export LD_LIBRARY_PATH=/opt/dynamo/native:${LD_LIBRARY_PATH:-}
if [[ ${DYNAMO_IMAGE_PLATFORM} == metax ]]; then
    export UCX_PROTO_ENABLE=${UCX_PROTO_ENABLE:-y}
    export UCX_TLS=${UCX_TLS:-maca_ipc,maca_copy,tcp}
fi
# nixl-cu13's wheel keeps its C API outside the Python package directory.
if [[ ${DYNAMO_IMAGE_PLATFORM} == cuda ]]; then
    nixl_lib=$(python -c 'import importlib.metadata as m; from pathlib import Path; d=m.distribution("nixl-cu13"); print(next(str(Path(d.locate_file(p)).parent) for p in d.files if str(p).endswith("/libnixl_capi.so")))')
    nccl_lib=$(python -c 'import importlib.metadata as m; from pathlib import Path; d=m.distribution("nvidia-nccl-cu13"); print(next(str(Path(d.locate_file(p)).parent) for p in d.files if str(p).endswith("/libnccl.so.2")))')
    export LD_LIBRARY_PATH="$nixl_lib:$nccl_lib:$LD_LIBRARY_PATH"
fi
exec "$@"
