---
name: adapter-diagnose-regression
description: Diagnoses an adapter regression using paired native and Dynamo evidence without hiding failures.
license: Apache-2.0
metadata:
  author: HEYI
  tags: [adapter, workflow, validation]
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# adapter-diagnose-regression

Compare native and Dynamo runs with the same locked image, model and topology. Record paired raw evidence and hashes before attributing failure to the native engine. For interrupted attempts, observe-resources/recover checks UID or boot/start-qualified PID ownership before retrying; never clean a replacement resource by name. Preserve every failed attempt. Environment/native failures are recorded, not hidden by changing vendor versions.

Canonical instructions: `adapter/AGENTS.md`. Commands and recovery: `adapter/workflow/README.md`. Structured contracts: `adapter/contracts/schemas/`.
