// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
package dynamo

import (
	"encoding/json"
	"os/exec"
	"testing"

	configv1alpha1 "github.com/ai-dynamo/dynamo/deploy/operator/api/config/v1alpha1"
	"github.com/ai-dynamo/dynamo/deploy/operator/api/v1beta1"
	"github.com/ai-dynamo/dynamo/deploy/operator/internal/consts"
	"github.com/ai-dynamo/dynamo/deploy/operator/internal/platform"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
)

func TestM08RenderedPodContracts(t *testing.T) {
	for _, id := range []string{"cuda", "metax", "ascend"} {
		for _, mode := range []string{"aggregate", "pd"} {
			t.Run(id+"/"+mode, func(t *testing.T) {
				t.Log("Render the actual handoff manifest, then run the production Pod renderer")
				output, err := exec.Command("python3", "../../../../adapter/m08/render.py", "--platform", id, "--mode", mode, "--model-pvc", "models").Output()
				require.NoError(t, err)
				var dgd v1beta1.DynamoGraphDeployment
				require.NoError(t, json.Unmarshal(output, &dgd))
				for i := range dgd.Spec.Components {
					c := &dgd.Spec.Components[i]
					before := c.DeepCopy()
					config := &configv1alpha1.OperatorConfiguration{}
					pod, err := GenerateBasePodSpec(c, BackendFrameworkVLLM, nil, dgd.Name, dgd.Namespace, RoleMain, 1, config, consts.MultinodeDeploymentTypeGrove, c.ComponentName, nil, staticContainerGPUCount(1))
					require.NoError(t, err)
					require.Equal(t, before, c, "render must not mutate input")
					require.Equal(t, []string{"/opt/dynamo/bin/entrypoint.sh"}, pod.Containers[0].Command)
					owner, err := platform.PodPlatform(pod)
					require.NoError(t, err)
					shape, err := ResolveGPUShape(t.Context(), nil, dgd.Namespace, c, []PodSpecMultiplicity{{PodSpec: pod, Count: 1}})
					require.NoError(t, err)
					if c.ComponentType == v1beta1.ComponentTypeFrontend {
						require.Empty(t, owner)
						require.Equal(t, GPUShape{}, shape)
					} else {
						require.Equal(t, id, owner)
						require.Equal(t, GPUShape{GPUsPerEngine: 1, GPUsPerReplica: 1}, shape)
						require.NotNil(t, pod.Containers[0].ReadinessProbe)
						require.NotNil(t, pod.Containers[0].StartupProbe)
						require.Equal(t, before.PodTemplate.Spec.Containers[0].Resources, pod.Containers[0].Resources)
					}
				}
			})
		}
	}
}

func TestVendorComponentRejectsUnimplementedSharing(t *testing.T) {
	for _, id := range []string{"metax", "ascend"} {
		for _, feature := range []string{"gms", "failover", "checkpoint", "dra"} {
			t.Run(id+"/"+feature, func(t *testing.T) {
				t.Log("Fail before rendering CUDA-specific sharing or restore paths")
				c := &v1beta1.DynamoComponentDeploymentSharedSpec{PodTemplate: &corev1.PodTemplateSpec{Spec: corev1.PodSpec{Containers: []corev1.Container{{Name: "main", Env: []corev1.EnvVar{{Name: "DYN_PLATFORM", Value: id}}}}}}}
				c.Experimental = &v1beta1.ExperimentalSpec{}
				switch feature {
				case "gms":
					c.Experimental.GPUMemoryService = &v1beta1.GPUMemoryServiceSpec{}
				case "failover":
					c.Experimental.Failover = &v1beta1.FailoverSpec{}
				case "checkpoint":
					c.Experimental.Checkpoint = &v1beta1.ComponentCheckpointConfig{}
				case "dra":
					c.PodTemplate.Spec.Containers[0].Resources.Claims = []corev1.ResourceClaim{{Name: "device"}}
				}
				_, err := ResolveComponentPlatform(c)
				require.Error(t, err)
			})
		}
	}
}
