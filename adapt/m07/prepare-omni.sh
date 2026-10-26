#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Experimental Omni overlay; leaves the pinned engine and torch installation intact.
set -euo pipefail
platform=${1:?usage: prepare-omni.sh cuda|metax|ascend}
python=${DYNAMO_PYTHON:-/opt/dynamo/venv/bin/python}
overlay=${OMNI_OVERLAY:-/opt/dynamo/omni-overlay}
"$python" -c 'import sys; from dynamo.common.platform.versions import validate_vllm_versions; validate_vllm_versions(sys.argv[1])' "$platform"
case "$platform" in
  cuda)
    "$python" -c 'import importlib.metadata as m; print("Existing image Omni:", m.version("vllm-omni"))'
    exit 0
    ;;
  metax) omni=0.26.0; diffusers=0.38.0 ;;
  ascend) omni=0.23.0rc1; diffusers=0.37.1 ;;
  *) exit 2 ;;
esac
if [[ -e "$overlay" ]]; then
  echo "Overlay already exists: $overlay; inspect it or choose OMNI_OVERLAY for a new directory."
  exit 1
fi
"$python" -m pip install --no-deps --target "$overlay" \
  "vllm-omni==$omni" "diffusers==$diffusers" \
  aenum==3.1.16 omegaconf==2.3.0 antlr4-python3-runtime==4.9.3 \
  janus==2.0.0 accelerate==1.12.0 prettytable==3.16.0 soundfile==0.13.1 av==16.1.0
if [[ "$platform" == metax ]]; then
  "$python" -m pip install --no-deps --target "$overlay" cache-dit==1.3.0 gguf==0.17.1
  SETUPTOOLS_SCM_PRETEND_VERSION=0.26.0 "$python" -m pip install \
    --no-deps --no-build-isolation --target "$overlay" \
    'https://api.github.com/repos/MetaX-MACA/vllm-omni-metax/tarball/35cc3687bba127a760331bea9466bf554dce028b'
fi
echo "For Ascend, source the CANN set_env.sh first. Preserve its PYTHONPATH:"
printf 'export PYTHONPATH=%q:"${PYTHONPATH:-}"\n' "$overlay"
echo "Installation is not Omni end-to-end certification; see followup.md."
