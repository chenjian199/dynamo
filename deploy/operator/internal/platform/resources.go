// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Package platform describes the whole-device Device Plugin contract. It does not
// infer physical device counts from virtual-device or memory quota resources.
package platform

import (
	"fmt"
	"sort"
	"strings"

	corev1 "k8s.io/api/core/v1"
)

const (
	CUDAResource   corev1.ResourceName = "nvidia.com/gpu"
	MetaXResource  corev1.ResourceName = "metax-tech.com/gpu"
	AscendResource corev1.ResourceName = "huawei.com/Ascend910"
)

// ResourcePlatform recognizes exact, case-sensitive whole-device resource names.
func ResourcePlatform(name corev1.ResourceName) string {
	switch name {
	case CUDAResource:
		return "cuda"
	case MetaXResource:
		return "metax"
	case AscendResource:
		return "ascend"
	default:
		return ""
	}
}

// CheckResources refuses unrecognized vendor allocation units instead of
// reporting zero GPUs or treating memory/core quotas as physical devices.
func CheckResources(resources corev1.ResourceRequirements) error {
	names := make(map[string]bool)
	for _, list := range []corev1.ResourceList{resources.Requests, resources.Limits} {
		for name := range list {
			text := string(name)
			if strings.HasPrefix(text, "metax-tech.com/") || strings.HasPrefix(text, "huawei.com/") {
				names[text] = true
			}
		}
	}
	// Sort diagnostics so admission and reconciliation report the same error.
	ordered := make([]string, 0, len(names))
	for name := range names {
		ordered = append(ordered, name)
	}
	sort.Strings(ordered)
	for _, text := range ordered {
		name := corev1.ResourceName(text)
		if ResourcePlatform(name) == "" {
			return fmt.Errorf("resource %q has an unsupported allocation unit; use whole-device metax-tech.com/gpu or huawei.com/Ascend910", name)
		}
		for _, list := range []corev1.ResourceList{resources.Requests, resources.Limits} {
			if q, ok := list[name]; ok && (q.Sign() < 0 || q.CmpInt64(q.Value()) != 0) {
				return fmt.Errorf("resource %q must have a non-negative whole-device count", name)
			}
		}
		if req, ok := resources.Requests[name]; ok {
			if limit, found := resources.Limits[name]; found && req.Cmp(limit) != 0 {
				return fmt.Errorf("resource %q requests and limits must match", name)
			}
		}
	}
	return nil
}

// ContainerPlatform resolves resource ownership and checks an explicit
// DYN_PLATFORM. Missing CUDA identity retains the legacy default. Other vendor
// workers must declare it literally so admission can verify their intent.
func ContainerPlatform(container corev1.Container) (string, error) {
	if err := CheckResources(container.Resources); err != nil {
		return "", err
	}
	declared, declarations, indirect := "", 0, false
	for _, env := range container.Env {
		if env.Name == "DYN_PLATFORM" {
			declared, indirect = env.Value, env.ValueFrom != nil
			declarations++
		}
	}
	// Resolve each accelerator namespace, including legacy CUDA MIG resources.
	owner := ""
	for _, list := range []corev1.ResourceList{container.Resources.Requests, container.Resources.Limits} {
		names := make([]string, 0, len(list))
		for name := range list {
			names = append(names, string(name))
		}
		sort.Strings(names)
		for _, name := range names {
			id := ResourcePlatform(corev1.ResourceName(name))
			if strings.HasPrefix(name, "nvidia.com/mig-") {
				id = "cuda"
			}
			if id == "" {
				continue
			}
			if owner != "" && owner != id {
				return "", fmt.Errorf("container requests devices from different platforms")
			}
			owner = id
		}
	}
	if owner == "metax" || owner == "ascend" || declared == "metax" || declared == "ascend" {
		if declarations != 1 || indirect {
			return "", fmt.Errorf("vendor containers require one literal DYN_PLATFORM matching their device resource")
		}
	}
	if owner != "" && declared != "" && owner != declared {
		return "", fmt.Errorf("DYN_PLATFORM %q conflicts with %s device resources", declared, owner)
	}
	if owner != "" {
		return owner, nil
	}
	return declared, nil
}

// PodPlatform checks all regular and init containers without changing the Pod.
// CPU helpers are allowed alongside one accelerator platform.
func PodPlatform(pod *corev1.PodSpec) (string, error) {
	owner := ""
	containers := append(append([]corev1.Container{}, pod.Containers...), pod.InitContainers...)
	for _, container := range containers {
		id, err := ContainerPlatform(container)
		if err != nil {
			return "", fmt.Errorf("container %q: %w", container.Name, err)
		}
		if id != "cuda" && id != "metax" && id != "ascend" {
			continue
		}
		if owner != "" && owner != id {
			return "", fmt.Errorf("pod mixes %s and %s platforms", owner, id)
		}
		owner = id
	}
	// DRA semantics are deliberately separate from scalar whole-device support.
	if owner == "metax" || owner == "ascend" {
		if len(pod.ResourceClaims) != 0 {
			return "", fmt.Errorf("%s DRA is not supported by this deployment path", owner)
		}
		for _, c := range containers {
			if len(c.Resources.Claims) != 0 {
				return "", fmt.Errorf("%s DRA is not supported by this deployment path", owner)
			}
		}
	}
	return owner, nil
}
