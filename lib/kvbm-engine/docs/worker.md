<!--
SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
SPDX-License-Identifier: Apache-2.0
Derived from NVIDIA Dynamo. Modified by HEYI.
-->

# Worker Module

The worker module defines execution primitives for data transfers between
storage tiers. Workers own the physical resources (transfer managers, layout
handles) needed to move blocks via RDMA, local copy, or object storage.

## Trait Hierarchy

```text
WorkerTransfers          Worker
  (execution)       (metadata + handles)
       │                    │
       └────────┬───────────┘
                │
         ObjectBlockOps
          (G4 storage)
```

- **`WorkerTransfers`** – core execution trait. Provides `execute_local_transfer`,
  `execute_remote_onboard`, `execute_remote_offload`, `connect_remote`, and
  `execute_remote_onboard_for_instance`.
- **`Worker`** – extends `WorkerTransfers + ObjectBlockOps`. Adds layout handle
  accessors (`g1_handle`, `g2_handle`, `g3_handle`) and metadata import/export.

## PhysicalWorker (aka DirectWorker)

`PhysicalWorker` is the fundamental single-worker implementation. It directly
owns a `TransferManager` and layout handles for executing data movement.

### Builder

```rust,ignore
let worker = PhysicalWorker::builder()
    .manager(transfer_manager)   // required
    .g1_handle(g1)               // optional – GPU tier
    .g2_handle(g2)               // optional – host tier
    .g3_handle(g3)               // optional – disk tier
    .rank(0)                     // optional – for SPMD key prefixing
    .object_client(s3_client)    // optional – for G4 operations
    .build()?;
```

| Field | Required | Purpose |
|-------|----------|---------|
| `manager` | yes | `TransferManager` for executing transfers |
| `g1_handle` | no | GPU/HBM layout handle |
| `g2_handle` | no | Host/pinned-DRAM layout handle |
| `g3_handle` | no | Disk/NVMe layout handle |
| `rank` | no | Worker rank for SPMD key prefixing |
| `object_client` | no | G4 object storage client |

`DirectWorker` is a compatibility alias for `PhysicalWorker`.

### Execution State vs Coordination State

PhysicalWorker maintains **execution state** – the handles and manager needed
to actually perform transfers. This is distinct from **coordination state**
which the leader tracks in `CoordinatedWorker`. When a leader wraps a
PhysicalWorker in a CoordinatedWorker, handles exist in both places
intentionally: PhysicalWorker needs them to call TransferManager, while
CoordinatedWorker provides a uniform API for both local and remote workers.

## CoordinatedWorker

`CoordinatedWorker` is the leader's view of a worker. It wraps any `Worker`
implementation and adds coordination state:

- Local layout handles (populated via `apply_layout_response`)
- Remote handle mappings for cross-leader RDMA transfers
- Worker rank and host instance tracking

This wrapper lets the leader use the same API regardless of whether the
underlying worker is local (`PhysicalWorker`) or remote (`VeloWorkerClient`).

## VeloWorkerClient / VeloWorkerService

The Velo (RPC) layer enables remote worker execution:

- **`VeloWorkerService`** – wraps a `PhysicalWorker` and exposes RPC handlers
  for `execute_local_transfer`, `export_metadata`, `import_metadata`, etc.
- **`VeloWorkerClient`** – implements `WorkerTransfers` by sending RPC
  requests to a remote `VeloWorkerService`.

Together they allow the leader to drive workers on remote nodes as if they
were local.

## Local platform workers

`TransferManager::from_native(NativeTransfers::new(...))` selects a local SDK
executor without constructing a CUDA context or NIXL agent. Register
`PhysicalLayout::from_native(NativeLayout::allocate(...))` for contiguous buffers,
`allocate_layers(...)` for separate NHD/HND layer tensors, or `disk(...)` for
buffered anonymous files. `from_allocations(...)` retains existing M04 allocation
owners without changing their contents. Engine writes must finish before a
transfer; the caller retains exclusive block leases until notification completion.

The same `PhysicalWorker` and `WorkerTransfers` interfaces execute G1/G2/G3
operations. Layouts carry explicit platform/context identity and cannot be mixed
with legacy CUDA/NIXL layouts. Native layouts cannot be exported/imported for
remote transfer. `TransferManager::context()` and `nixl_agent()` return `Result`
and report an unavailable backend for local native managers. Local coordinators
can obtain G1/G2/G3 handles through `Worker`; remote metadata is not a substitute
for local handle setup.

## Replicated local cache

`ReplicatedDataWorker::with_events(...)` validates rank and tier ownership. Only
rank zero owns G2/G3. Onboard waits for rank zero's local copy, then broadcasts G1;
other ranks receive through the same ordered collective sequence. Offload and
host/disk movement execute only on rank zero. G1-to-G1 local copies run on every
rank. Dropping a notification does not cancel the queued work or release its
allocation owners. The enclosing worker group must aggregate all rank completions.

`NativeCollectives` implements this contract using NCCL, MCCL, or HCCL selected
from the explicit M04 device identity. Each communicator owns a separate stream;
real SDK events establish local completion. The correctness fallback stages a
canonical block and preserves each rank's NHD/HND layout. Partial-layer updates
preserve other layers but do not implement a fused layer pipeline. Bootstrap
requires a supervisor timeout; a failed collective with pending device work
terminates the worker rather than acknowledging success or releasing live leases.
G4 and remote replicated operations are not enabled.

The hardware example `native_replicated` exercises the independent engine,
physical and kernel crates. This does not switch the existing Python vLLM
connector from its `dynamo-llm/block_manager` implementation or certify a complete
MLA model inference configuration.
