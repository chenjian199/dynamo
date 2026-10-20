// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
// Derived from NVIDIA Dynamo. Modified by HEYI.
// SPDX-License-Identifier: Apache-2.0

//! Replicated data worker for MLA (Multi-head Latent Attention) scenarios.
//!
//! In MLA architectures, KV blocks are replicated across all workers rather than
//! sharded. This means only rank 0 needs G2/G3 storage - other ranks receive
//! data via broadcast from rank 0 after it loads from G2/G3.
//!
//! # Architecture
//!
//! ```text
//! Rank 0:   G3 (disk) ←→ G2 (host) ←→ G1 (GPU) ───broadcast──→ Other ranks G1
//! Rank 1-N: [no G2/G3]                G1 (GPU) ←──────────────────────┘
//! ```
//!
//! # Transfer Semantics
//!
//! | Operation | Behavior |
//! |-----------|----------|
//! | G2/G3 → G1 (onboard) | Rank 0 transfers, then broadcasts to all ranks |
//! | G1 → G2/G3 (offload) | Rank 0 only (other ranks don't have G2/G3) |
//! | G2 ↔ G3 | Rank 0 only |
//! | G1 → G1 (local) | All ranks execute (data is replicated) |

use super::*;

use crate::KvbmRuntime;
use crate::collectives::CollectiveOps;
use anyhow::Result;

use std::sync::Arc;

/// Replicated data worker for MLA scenarios.
///
/// Only rank 0 has G2/G3 storage. When loading data to G1, rank 0 transfers
/// from G2/G3 and then broadcasts to all other ranks via collective operations.
///
/// # Requirements
///
/// - Workers must be initialized such that only rank 0 has G2/G3 handles
/// - A [`CollectiveOps`] implementation must be provided for broadcasting
///
/// # Trait Implementations
///
/// - [`WorkerTransfers`]: Specialized routing based on source/destination tiers
/// - [`Worker`]: Delegates local layout identity to the physical worker
/// - [`ObjectBlockOps`]: G4 is not enabled for this local replicated path
pub struct ReplicatedDataWorker {
    inner: Arc<PhysicalWorker>,
    events: Arc<::velo::EventManager>,
    runtime: tokio::runtime::Handle,
    order: parking_lot::Mutex<Option<tokio::sync::oneshot::Receiver<()>>>,
    pending: Arc<tokio::sync::Semaphore>,
    collective: Arc<dyn CollectiveOps>,
}

impl ReplicatedDataWorker {
    /// Build a local replicated worker with validated rank and tier ownership.
    pub fn new(
        worker: Arc<PhysicalWorker>,
        runtime: Arc<KvbmRuntime>,
        collective: Arc<dyn CollectiveOps>,
    ) -> Result<Self> {
        Self::with_events(worker, runtime.event_system(), runtime.handle(), collective)
    }

    pub fn with_events(
        worker: Arc<PhysicalWorker>,
        events: Arc<::velo::EventManager>,
        runtime: tokio::runtime::Handle,
        collective: Arc<dyn CollectiveOps>,
    ) -> Result<Self> {
        anyhow::ensure!(
            collective.world_size() > 0 && collective.rank() < collective.world_size(),
            "invalid collective rank"
        );
        anyhow::ensure!(
            worker.rank() == Some(collective.rank()),
            "worker/collective rank mismatch"
        );
        anyhow::ensure!(
            worker.g1_handle().is_some(),
            "replicated worker requires G1"
        );
        if collective.rank() != 0 {
            anyhow::ensure!(
                worker.g2_handle().is_none() && worker.g3_handle().is_none(),
                "only rank zero may own replicated G2/G3"
            );
        }
        Ok(Self {
            inner: worker,
            events,
            runtime,
            collective,
            order: parking_lot::Mutex::new(None),
            pending: Arc::new(tokio::sync::Semaphore::new(64)),
        })
    }

    /// Get access to the underlying physical worker.
    pub fn inner(&self) -> &PhysicalWorker {
        &self.inner
    }

    /// Get the rank of the underlying worker.
    pub fn rank(&self) -> usize {
        self.collective.rank()
    }

    fn broadcast(
        &self,
        xfer_completion: TransferCompleteNotification,
        dst: LogicalLayoutHandle,
        dst_block_ids: Arc<[BlockId]>,
        options: kvbm_physical::transfer::TransferOptions,
        permit: tokio::sync::OwnedSemaphorePermit,
    ) -> Result<TransferCompleteNotification> {
        let event = self.events.new_event()?;
        let awaiter = self.events.awaiter(event.handle())?;
        let collective = self.collective.clone();
        let (done, next) = tokio::sync::oneshot::channel();
        let previous = self.order.lock().replace(next);
        self.runtime.spawn(async move {
            let _permit = permit;
            if let Some(previous) = previous {
                let _ = previous.await;
            }
            let result = async {
                xfer_completion.await?;
                collective
                    .broadcast(
                        dst,
                        dst,
                        &dst_block_ids,
                        &dst_block_ids,
                        options.layer_range,
                    )?
                    .await
            }
            .await;
            match result {
                Ok(()) => {
                    let _ = event.trigger();
                }
                Err(error) => {
                    let _ = event.poison(error.to_string());
                }
            }
            let _ = done.send(());
        });
        Ok(TransferCompleteNotification::from_awaiter(awaiter))
    }
}

impl WorkerTransfers for ReplicatedDataWorker {
    fn execute_local_transfer(
        &self,
        src: LogicalLayoutHandle,
        dst: LogicalLayoutHandle,
        src_block_ids: Arc<[BlockId]>,
        dst_block_ids: Arc<[BlockId]>,
        options: kvbm_physical::transfer::TransferOptions,
    ) -> Result<TransferCompleteNotification> {
        let is_rank0 = self.rank() == 0;
        let use_bcast = dst == LogicalLayoutHandle::G1;
        anyhow::ensure!(
            src != LogicalLayoutHandle::G4 && dst != LogicalLayoutHandle::G4,
            "replicated G4 transfers are not enabled"
        );
        anyhow::ensure!(
            src_block_ids.len() == dst_block_ids.len(),
            "block counts differ"
        );

        if src == LogicalLayoutHandle::G1 && dst == LogicalLayoutHandle::G1 {
            return self.inner.execute_local_transfer(
                src,
                dst,
                src_block_ids,
                dst_block_ids.clone(),
                options,
            );
        }

        if !is_rank0 && !use_bcast {
            Ok(TransferCompleteNotification::completed())
        } else if is_rank0 {
            let permit = if use_bcast {
                Some(
                    self.pending
                        .clone()
                        .try_acquire_owned()
                        .map_err(|_| anyhow::anyhow!("replicated broadcast queue is full"))?,
                )
            } else {
                None
            };
            let xfer_completion = self.inner.execute_local_transfer(
                src,
                dst,
                src_block_ids,
                dst_block_ids.clone(),
                options.clone(),
            )?;

            if let Some(permit) = permit {
                self.broadcast(xfer_completion, dst, dst_block_ids, options, permit)
            } else {
                Ok(xfer_completion)
            }
        } else {
            let permit = self
                .pending
                .clone()
                .try_acquire_owned()
                .map_err(|_| anyhow::anyhow!("replicated broadcast queue is full"))?;
            let xfer_completion = TransferCompleteNotification::completed();
            self.broadcast(xfer_completion, dst, dst_block_ids, options, permit)
        }
    }

    #[expect(unused_variables)]
    fn execute_remote_onboard(
        &self,
        src: RemoteDescriptor,
        dst: LogicalLayoutHandle,
        dst_block_ids: Arc<[BlockId]>,
        options: kvbm_physical::transfer::TransferOptions,
    ) -> Result<TransferCompleteNotification> {
        anyhow::bail!("replicated remote transfers are not enabled")
    }

    #[expect(unused_variables)]
    fn execute_remote_offload(
        &self,
        src: LogicalLayoutHandle,
        src_block_ids: Arc<[BlockId]>,
        dst: RemoteDescriptor,
        options: kvbm_physical::transfer::TransferOptions,
    ) -> Result<TransferCompleteNotification> {
        anyhow::bail!("replicated remote transfers are not enabled")
    }

    fn connect_remote(
        &self,
        instance_id: InstanceId,
        metadata: Vec<SerializedLayout>,
    ) -> Result<ConnectRemoteResponse> {
        // Use the shared implementation
        self.inner.connect_remote(instance_id, metadata)
    }

    fn has_remote_metadata(&self, instance_id: InstanceId) -> bool {
        self.inner.has_remote_metadata(instance_id)
    }

    #[expect(unused_variables)]
    fn execute_remote_onboard_for_instance(
        &self,
        instance_id: InstanceId,
        remote_logical_type: LogicalLayoutHandle,
        src_block_ids: Vec<BlockId>,
        dst: LogicalLayoutHandle,
        dst_block_ids: Arc<[BlockId]>,
        options: kvbm_physical::transfer::TransferOptions,
    ) -> Result<TransferCompleteNotification> {
        anyhow::bail!("replicated remote transfers are not enabled")
    }
}

impl Worker for ReplicatedDataWorker {
    fn g1_handle(&self) -> Option<LayoutHandle> {
        self.inner.g1_handle()
    }
    fn g2_handle(&self) -> Option<LayoutHandle> {
        self.inner.g2_handle()
    }
    fn g3_handle(&self) -> Option<LayoutHandle> {
        self.inner.g3_handle()
    }
    fn export_metadata(&self) -> Result<SerializedLayoutResponse> {
        Worker::export_metadata(self.inner.as_ref())
    }
    fn import_metadata(&self, metadata: SerializedLayout) -> Result<ImportMetadataResponse> {
        Worker::import_metadata(self.inner.as_ref(), metadata)
    }
}

impl ObjectBlockOps for ReplicatedDataWorker {
    fn has_blocks(
        &self,
        keys: Vec<SequenceHash>,
    ) -> BoxFuture<'static, Vec<(SequenceHash, Option<usize>)>> {
        Box::pin(async move { keys.into_iter().map(|k| (k, None)).collect() })
    }
    fn put_blocks(
        &self,
        keys: Vec<SequenceHash>,
        _src: LogicalLayoutHandle,
        _ids: Vec<BlockId>,
    ) -> BoxFuture<'static, Vec<Result<SequenceHash, SequenceHash>>> {
        Box::pin(async move { keys.into_iter().map(Err).collect() })
    }
    fn get_blocks(
        &self,
        keys: Vec<SequenceHash>,
        _dst: LogicalLayoutHandle,
        _ids: Vec<BlockId>,
    ) -> BoxFuture<'static, Vec<Result<SequenceHash, SequenceHash>>> {
        Box::pin(async move { keys.into_iter().map(Err).collect() })
    }
}
