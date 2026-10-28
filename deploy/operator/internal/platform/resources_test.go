// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
package platform

import (
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	"testing"
)

func TestContainerPlatform(t *testing.T) {
	tests := []struct {
		name, resource, identity, count, want string
		fail                                  bool
	}{
		{"legacy CUDA", "nvidia.com/gpu", "", "2", "cuda", false},
		{"CUDA MIG", "nvidia.com/mig-1g.10gb", "cuda", "1", "cuda", false},
		{"MetaX", "metax-tech.com/gpu", "metax", "2", "metax", false},
		{"Ascend", "huawei.com/Ascend910", "ascend", "2", "ascend", false},
		{"missing identity", "huawei.com/Ascend910", "", "1", "", true},
		{"wrong identity", "metax-tech.com/gpu", "cuda", "1", "", true},
		{"virtual quota", "metax-tech.com/sgpu", "metax", "16", "", true},
		{"virtual NPU", "huawei.com/Ascend910-4c", "ascend", "1", "", true},
		{"memory quota", "metax-tech.com/vmemory", "metax", "4", "", true},
		{"fractional", "huawei.com/Ascend910", "ascend", "500m", "", true},
		{"negative", "huawei.com/Ascend910", "ascend", "-1", "", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Log("Resolve the declared platform and allocation unit")
			c := corev1.Container{Resources: corev1.ResourceRequirements{Limits: corev1.ResourceList{corev1.ResourceName(tt.resource): resource.MustParse(tt.count)}}}
			if tt.identity != "" {
				c.Env = []corev1.EnvVar{{Name: "DYN_PLATFORM", Value: tt.identity}}
			}
			before := c.DeepCopy()
			got, err := ContainerPlatform(c)
			if tt.fail {
				require.Error(t, err)
			} else {
				require.NoError(t, err)
				require.Equal(t, tt.want, got)
			}
			require.Equal(t, *before, c)
		})
	}
}

func TestPodPlatformRejectsConflictingInitDevice(t *testing.T) {
	t.Log("CPU helpers may accompany a MetaX worker, but not Ascend init devices")
	pod := corev1.PodSpec{Containers: []corev1.Container{{Name: "main", Env: []corev1.EnvVar{{Name: "DYN_PLATFORM", Value: "metax"}}}}, InitContainers: []corev1.Container{{Name: "init", Env: []corev1.EnvVar{{Name: "DYN_PLATFORM", Value: "cpu"}}}}}
	id, err := PodPlatform(&pod)
	require.NoError(t, err)
	require.Equal(t, "metax", id)
	pod.InitContainers[0].Env[0].Value = "ascend"
	_, err = PodPlatform(&pod)
	require.ErrorContains(t, err, "mixes")
}
