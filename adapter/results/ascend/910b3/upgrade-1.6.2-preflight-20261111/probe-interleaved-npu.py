# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Diagnostic zero-copy NPU storage probe, not packaged-image certification."""
import importlib.util,json
import torch,torch_npu
spec=importlib.util.spec_from_file_location('layout','/probe/platform_events.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
x=torch.arange(7*2*128*8*16,dtype=torch.float32).reshape(7,2,128,8,16).npu()
k,v=x[:,0],x[:,1];merged=m.coalesce_interleaved_kv(k,v)
assert merged.data_ptr()==k.data_ptr() and torch.equal(merged,x)
merged[3,1].fill_(-17);torch.npu.synchronize();assert torch.all(v[3]==-17)
k[4].fill_(31);torch.npu.synchronize();assert torch.all(merged[4,0]==31)
assert m.block_axis(merged,7)==0
print(json.dumps({'passed':True,'format':torch_npu.get_npu_format(x),'original_shape':list(k.shape),'original_stride':list(k.stride()),'combined_shape':list(merged.shape),'combined_stride':list(merged.stride()),'zero_copy':True,'scope':'source helper diagnostic on NPU; requires sealed image regression'}))
