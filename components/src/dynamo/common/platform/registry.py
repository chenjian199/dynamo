# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
import importlib.metadata
import threading


class PlatformError(RuntimeError):
    def __init__(self, code, provider, message, *, retryable=False):
        self.code = code
        self.provider = provider
        self.message = message
        self.retryable = retryable
        super().__init__(f"{provider}: {code}: {message}")

    def to_dict(self):
        return dict(
            code=self.code,
            provider=self.provider,
            message=self.message,
            retryable=self.retryable,
        )


class PlatformRegistry:
    """One immutable provider/context per registry, with SDKs loaded on selection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._selected = None
        self._descriptor = None
        self._providers = {
            name: f"dynamo.common.platform.providers:{name}"
            for name in ("cpu", "cuda", "metax")
        }
        for entry in importlib.metadata.entry_points(group="dynamo.platforms"):
            self.register(entry.name, entry.load)

    def register(self, name, loader):
        if name in self._providers:
            raise PlatformError(
                "duplicate_provider", name, "platform ID already registered"
            )
        self._providers[name] = loader

    def prepare(self, platform_id, context=None, required=()):
        context = dict(context or {})
        with self._lock:
            key = (platform_id, context)
            if self._selected is not None and key != self._selected:
                raise PlatformError(
                    "configuration_conflict",
                    platform_id,
                    "provider/context already initialized",
                )
            if self._descriptor is None:
                loader = self._providers.get(platform_id)
                if loader is None:
                    raise PlatformError(
                        "unknown_platform", platform_id, "no registered provider"
                    )
                try:
                    if isinstance(loader, str):
                        module, name = loader.split(":")
                        provider = getattr(importlib.import_module(module), name)
                    else:
                        provider = loader()
                    descriptor = provider(context)
                except PlatformError:
                    raise
                except Exception as error:
                    raise PlatformError(
                        "probe_failed", platform_id, str(error)
                    ) from error
                if (
                    descriptor.get("schema_version") != 1
                    or descriptor.get("platform_id") != platform_id
                ):
                    raise PlatformError(
                        "invalid_descriptor",
                        platform_id,
                        "provider returned incompatible identity/schema",
                    )
                self._descriptor = copy.deepcopy(descriptor)
                self._selected = key
            descriptor = copy.deepcopy(self._descriptor)
            for capability in required:
                report = descriptor.get("capabilities", {}).get(capability, {})
                if report.get("state") != "available":
                    raise PlatformError(
                        "missing_capability",
                        platform_id,
                        f"{capability}: {report.get('reason', 'unknown')}",
                    )
                if any(
                    context.get(k) != v for k, v in report.get("conditions", {}).items()
                ):
                    raise PlatformError("capability_mismatch", platform_id, capability)
            return descriptor
