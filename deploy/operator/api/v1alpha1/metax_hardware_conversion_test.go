// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

package v1alpha1

import (
	"testing"

	v1beta1 "github.com/ai-dynamo/dynamo/deploy/operator/api/v1beta1"
)

func TestMetaXHardwareRoundTrip(t *testing.T) {
	t.Log("Preserve explicit MetaX hardware through the existing hub-only hardware payload")
	original := newV1beta1DGDR()
	original.Spec.Hardware = &v1beta1.HardwareSpec{GPUSKU: v1beta1.GPUSKUTypeMetaXC500}
	spoke := &DynamoGraphDeploymentRequest{}
	if err := spoke.ConvertFrom(original); err != nil {
		t.Fatal(err)
	}

	t.Log("Restore the hardware identity without adding a new conversion annotation")
	restored := &v1beta1.DynamoGraphDeploymentRequest{}
	if err := spoke.ConvertTo(restored); err != nil {
		t.Fatal(err)
	}
	if restored.Spec.Hardware == nil || restored.Spec.Hardware.GPUSKU != v1beta1.GPUSKUTypeMetaXC500 {
		t.Fatalf("MetaX hardware not preserved: %+v", restored.Spec.Hardware)
	}
}
