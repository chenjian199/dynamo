---
name: heyi-copyright
description: Applies HEYI copyright headers to new and modified files in this Dynamo fork, preserving upstream attribution and licenses. Use when creating files, modifying upstream files, or reviewing copyright headers for HEYI changes.
license: Apache-2.0
metadata:
  author: HEYI
  tags: [copyright, spdx, heyi]
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# HEYI copyright headers

Apply the HEYI copyright policy in the repository root `AGENTS.md` to files
created or substantively modified for this fork. Inspect the existing header,
diff and provenance; an untracked or renamed file is not necessarily original.

For a modified NVIDIA file, retain the original copyright years, owner and
license. Add HEYI's modification copyright and an explicit modification notice:

```python
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Derived from NVIDIA Dynamo. Modified by HEYI.
```

The original NVIDIA line above is an example; preserve the actual source line.
If the original license is CC-BY-4.0 or another license, retain that identifier.

For a new file authored by HEYI, use:

```python
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
```

Use the actual creation/modification year for HEYI. New files copied or derived
from upstream retain the upstream notices and add HEYI's notice; do not label
them as exclusively HEYI-owned just because their path is new.

Match the file syntax: preserve the first-line shebang, use `#` for Python/shell,
`//` for Rust, HTML comments for plain Markdown, and frontmatter comments for Fern
pages. For JSON and other formats without comments, use a `<filename>.license`
sidecar rather than changing the data schema. Do not hand-edit generated files;
update their generator/template or appropriate license metadata.

Limit header updates to the requested changes. Leave unmodified upstream files,
vendor/reference trees and existing license/NOTICE files intact. Verify that the
diff preserves upstream notices, adds HEYI once, and keeps the original license.
