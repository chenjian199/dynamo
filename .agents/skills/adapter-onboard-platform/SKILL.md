---
name: adapter-onboard-platform
description: Establishes a new chip platform contract and runs environment preflight before adaptation.
license: Apache-2.0
metadata:
  author: HEYI
  tags: [adapter, workflow, validation]
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# adapter-onboard-platform

Resolve the canonical vendor stack and real SSH/container/Kubernetes environment. Store all new artifacts under adapter/results/<platform>/<chip>/<campaign>. Run init with explicit scope, then doctor. Bind each case to nodes, model manifests, a digest-pinned image and native topology. A missing asset blocks only affected cases; never upgrade drivers or infer unsupported hardware from missing infrastructure.

Canonical instructions: `adapter/AGENTS.md`. Commands and recovery: `adapter/workflow/README.md`. Structured contracts: `adapter/contracts/schemas/`.
