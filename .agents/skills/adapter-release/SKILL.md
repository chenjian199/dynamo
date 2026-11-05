---
name: adapter-release
description: Audits declared adapter scope and produces reproducible platform offline delivery records.
license: Apache-2.0
metadata:
  author: HEYI
  tags: [adapter, workflow, validation]
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# adapter-release

Generate current reports and check required scope/dependency evidence, accepted exceptions and remaining issues. Invoke deliver with the frozen M02/M10 recipe: build, dependencies, export, archive verification, import, smoke and seal. Keep outputs under the campaign results directory. Imported image IDs and versions must match before release; archive existence is insufficient. Never rewrite historical sealed packages. Report real-AI or hardware paths that were deferred.

Canonical instructions: `adapter/AGENTS.md`. Commands and recovery: `adapter/workflow/README.md`. Structured contracts: `adapter/contracts/schemas/`.
