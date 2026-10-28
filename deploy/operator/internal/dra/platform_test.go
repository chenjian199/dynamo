// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
package dra

import (
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/utils/ptr"
	"testing"
)

func TestVendorWholeDeviceAccounting(t *testing.T) {
	for _, name := range []corev1.ResourceName{"nvidia.com/gpu", "metax-tech.com/gpu", "huawei.com/Ascend910"} {
		t.Run(string(name), func(t *testing.T) {
			t.Log("Count requests/limits once and preserve init-container peak scheduling")
			main := corev1.Container{Name: "main", Resources: corev1.ResourceRequirements{Requests: corev1.ResourceList{name: resource.MustParse("2")}, Limits: corev1.ResourceList{name: resource.MustParse("2")}}}
			sidecar := corev1.Container{Name: "sidecar", RestartPolicy: ptr.To(corev1.ContainerRestartPolicyAlways), Resources: corev1.ResourceRequirements{Limits: corev1.ResourceList{name: resource.MustParse("1")}}}
			init := corev1.Container{Name: "init", Resources: corev1.ResourceRequirements{Limits: corev1.ResourceList{name: resource.MustParse("4")}}}
			pod := corev1.PodSpec{Containers: []corev1.Container{main, {Name: "cpu"}}, InitContainers: []corev1.Container{sidecar, init}}
			count, err := ResolvePodGPUCount(t.Context(), nil, "default", &pod)
			require.NoError(t, err)
			require.Equal(t, 5, count)
			t.Log("Replica multiplicity retains the same per-Pod units")
			total, err := ResolvePodSetGPUCount(t.Context(), nil, "default", []PodSpecMultiplicity{{PodSpec: &pod, Count: 3}})
			require.NoError(t, err)
			require.Equal(t, 15, total)
		})
	}
}

func TestUnknownVendorUnitsDoNotBecomeZero(t *testing.T) {
	for _, name := range []corev1.ResourceName{"metax-tech.com/sgpu", "huawei.com/Ascend910-4c"} {
		t.Run(string(name), func(t *testing.T) {
			t.Log("An unrecognized vendor allocation unit fails accounting")
			_, err := ExtractGPUCountFromResourceRequirements(corev1.ResourceRequirements{Limits: corev1.ResourceList{name: resource.MustParse("1")}})
			require.ErrorContains(t, err, "unsupported allocation unit")
		})
	}
}
