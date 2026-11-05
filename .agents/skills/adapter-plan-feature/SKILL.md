---
name: adapter-plan-feature
description: Plans a numbered adapter feature from its frozen contract and available evidence.
license: Apache-2.0
metadata:
  author: HEYI
  tags: [adapter, workflow, validation]
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# adapter-plan-feature

Read the frozen feature and its case recipes. Use required/optional/deferred/out_of_scope/not_applicable scope with accepted exception references, and explicit any-of choices. Supply run/observe/cleanup per case using exact entrypoints, then compile and seal-plan. Observation fields must satisfy deterministic checks using raw measurements/native controls. Do not substitute fixed passing flags or require every TP/DP/PP/EP combination to have been tested.

Canonical instructions: `adapter/AGENTS.md`. Commands and recovery: `adapter/workflow/README.md`. Structured contracts: `adapter/contracts/schemas/`.
