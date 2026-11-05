---
name: adapter-qualify-feature
description: Qualifies a feature using independent assertions and hashed campaign evidence.
license: Apache-2.0
metadata:
  author: HEYI
  tags: [adapter, workflow, validation]
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# adapter-qualify-feature

Qualify collected measurements with the frozen deterministic verifier and negative cases. Check command receipts, raw output, case assertions, runtime identity, cleanup observations and hashes. Optional-case failure is limited only when required cases pass. Inherit only explicitly reviewed exact-input evidence, rechecking original bytes. Never turn historical, mock, inherited or stale results into current hardware tests.

Canonical instructions: `adapter/AGENTS.md`. Commands and recovery: `adapter/workflow/README.md`. Structured contracts: `adapter/contracts/schemas/`.
