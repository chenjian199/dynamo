# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Isolate the Omni 0.23 registry API from the 0.26/0.28 API."""

try:
    from vllm_omni.config.config_factory import StageConfigFactory
except ModuleNotFoundError as exc:
    if exc.name != "vllm_omni.config.config_factory":
        raise
    from vllm_omni.config.stage_config import _PIPELINE_REGISTRY
    from vllm_omni.config.stage_config import StageConfigFactory as _LegacyFactory
    from vllm_omni.config.stage_config import register_pipeline as _register_pipeline

    # Only dynamic entries belong to Dynamo. Never mutate the builtin lazy map.
    OMNI_PIPELINES = _PIPELINE_REGISTRY._loaded

    def register_pipeline(pipeline, *, model_type):
        if pipeline.model_type != model_type:
            raise ValueError("dynamic Omni pipeline identity mismatch")
        _register_pipeline(pipeline)

    class StageConfigFactory:
        @staticmethod
        def get_pipeline_config(
            model, trust_remote_code=False, deploy_config_path=None
        ):
            import yaml

            key = None
            if deploy_config_path:
                with open(deploy_config_path) as stream:
                    deploy = yaml.safe_load(stream)
                if not isinstance(deploy, dict):
                    raise ValueError("Omni deploy config must be a mapping")
                key = deploy.get("pipeline")
            if key is None:
                key, _ = _LegacyFactory._auto_detect_model_type(
                    model, trust_remote_code=trust_remote_code
                )
            pipeline = _PIPELINE_REGISTRY.get(key)
            if pipeline is None:
                raise ValueError(
                    "Omni 0.23 requires an explicit registered pipeline for this model"
                )
            return pipeline

else:
    from vllm_omni.config import register_pipeline
    from vllm_omni.config.pipeline_registry import OMNI_PIPELINES

__all__ = ["OMNI_PIPELINES", "StageConfigFactory", "register_pipeline"]


def load_and_resolve_stage_configs(
    model, *, kwargs, trust_remote_code, deploy_config_path
):
    import inspect

    from vllm_omni.entrypoints.utils import load_and_resolve_stage_configs as resolve

    options = dict(
        kwargs=kwargs,
        trust_remote_code=trust_remote_code,
        deploy_config_path=deploy_config_path,
    )
    parameters = inspect.signature(resolve).parameters
    if "trust_remote_code" not in parameters:
        options.pop("trust_remote_code")
        options["kwargs"] = {**kwargs, "trust_remote_code": trust_remote_code}
    if "stage_configs_path" in parameters:
        options["stage_configs_path"] = None
    resolved = resolve(model, **options)
    if len(resolved) == 2:
        return (*resolved, None)
    return resolved
