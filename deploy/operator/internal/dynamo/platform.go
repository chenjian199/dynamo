// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

package dynamo

import (
	"fmt"
	"strings"

	"github.com/ai-dynamo/dynamo/deploy/operator/api/v1beta1"
	"github.com/ai-dynamo/dynamo/deploy/operator/internal/dra"
	"github.com/ai-dynamo/dynamo/deploy/operator/internal/platform"
)

// ResolveComponentPlatform checks the existing PodTemplate platform contract.
// A nil PodTemplate means legacy defaults; the component itself must be non-nil.
func ResolveComponentPlatform(component *v1beta1.DynamoComponentDeploymentSharedSpec) (string, error) {
	if component.PodTemplate == nil {
		return "", nil
	}
	id, err := platform.PodPlatform(&component.PodTemplate.Spec)
	if err != nil {
		return "", err
	}
	// These paths still contain CUDA-specific device sharing and restore logic.
	if id == "metax" || id == "ascend" {
		// Vendor workers must actually reserve devices through the plugin.
		if IsWorkerComponent(string(component.ComponentType)) {
			main := GetMainContainer(component)
			if main == nil {
				return "", fmt.Errorf("%s worker requires a main container with whole-device resources", id)
			}
			name := platform.MetaXResource
			if id == "ascend" {
				name = platform.AscendResource
			}
			_, requested := main.Resources.Requests[name]
			_, limited := main.Resources.Limits[name]
			if !requested && !limited {
				return "", fmt.Errorf("%s worker requires the %s whole-device resource", id, name)
			}
			count, err := dra.ExtractGPUCountFromResourceRequirements(main.Resources)
			if err != nil {
				return "", err
			}
			if count <= 0 {
				return "", fmt.Errorf("%s worker requires a positive whole-device resource request", id)
			}
		}
		// Soft sharing can reuse the whole-device resource name with quota units.
		for key := range component.PodTemplate.Annotations {
			if strings.HasPrefix(key, "huawei.com/scheduler.softShareDev.") {
				return "", fmt.Errorf("Ascend soft-sharing quotas are not whole-device allocations")
			}
		}

		if component.Experimental != nil && (component.Experimental.GPUMemoryService != nil || component.Experimental.Failover != nil) {
			return "", fmt.Errorf("%s Kubernetes GMS/failover is not supported; use the basic Device Plugin path", id)
		}
		if component.Experimental != nil && component.Experimental.Checkpoint != nil {
			return "", fmt.Errorf("%s Kubernetes checkpoint restore is not supported", id)
		}
	}
	return id, nil
}
