---
name: adapter-implement-feature
description: Implements a bounded chip adaptation feature while preserving upstream behavior and vendor baselines.
license: Apache-2.0
metadata:
  author: HEYI
  tags: [adapter, workflow, validation]
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# adapter-implement-feature

Use allowed implementation paths and preserve upstream/vendor baselines. Agent snapshots are separate from the shared checkout; apply only scoped changes against unchanged base SHA256. Do not edit protected contracts, verification code or version locks to make a case pass. Return per-case recipe proposals in tasks_json. The automate loop re-freezes each implementation iteration; it never treats model output as certification.

Canonical instructions: `adapter/AGENTS.md`. Commands and recovery: `adapter/workflow/README.md`. Structured contracts: `adapter/contracts/schemas/`.
