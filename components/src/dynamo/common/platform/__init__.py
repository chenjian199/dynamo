# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Platform contracts; importing this package never imports a device SDK."""

from dynamo.common.platform.registry import PlatformError, PlatformRegistry

registry = PlatformRegistry()

__all__ = ["PlatformError", "PlatformRegistry", "registry"]
