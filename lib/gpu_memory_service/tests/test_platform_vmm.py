# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real GMS ownership across spawned clients, using the selected vendor VMM."""

import ctypes
import multiprocessing
import os
import threading
from contextlib import ExitStack, closing, contextmanager

import pytest

pytestmark = [pytest.mark.post_merge, pytest.mark.integration, pytest.mark.gpu_1]


def _initialize():
    from gpu_memory_service.common.vmm import VMMDeviceType, get_vmm, init_vmm

    init_vmm(VMMDeviceType.from_str(os.environ.get("DYN_PLATFORM", "cuda")))
    vmm = get_vmm()
    device = int(os.environ.get("M07_DEVICE", "2"))
    vmm.runtime_set_device(device)
    vmm.ensure_initialized()
    return vmm, device


def _bytes(vmm, va, data=None):
    import mmap

    size = 4096
    with mmap.mmap(-1, size) as pages:
        host = ctypes.addressof(ctypes.c_char.from_buffer(pages))
        if data is not None:
            pages[:] = data
        vmm.host_register(host, size)
        stream = vmm.stream_create_nonblocking()
        try:
            if data is None:
                vmm.memcpy_d2h_async(host, va, size, stream)
            else:
                vmm.memcpy_h2d_async(va, host, size, stream)
            vmm.stream_synchronize(stream)
            return pages[:]
        finally:
            # Fail-stop before freeing host pages if a DMA cannot be drained.
            try:
                vmm.stream_synchronize(stream)
            except BaseException:
                os._exit(80)
            vmm.stream_destroy(stream)
            vmm.host_unregister(host)


def _client(path, action, record, pipe):
    try:
        from gpu_memory_service.common.locks import GrantedLockType, RequestedLockType
        from gpu_memory_service.v1.client.mapping import (
            reserve_and_install_mapping,
            unmap_mapping,
        )
        from gpu_memory_service.v1.client.memory_manager import GMSClientMemoryManager
        from gpu_memory_service.v1.client.session import _GMSClientSession

        vmm, device = _initialize()
        if action == "engine":
            import torch
            from vllm import LLM, SamplingParams

            torch.cuda.set_device(device)
            llm = LLM(
                model=os.environ["M07_MODEL"],
                worker_cls="gpu_memory_service.v1.integrations.vllm.worker.GMSV1Worker",
                enable_sleep_mode=True,
                enforce_eager=True,
                max_model_len=1024,
                max_num_seqs=2,
                gpu_memory_utilization=0.2,
                enable_prefix_caching=False,
            )
            sampling = SamplingParams(temperature=0, max_tokens=16)
            prompt = "The capital of France is"
            before = llm.generate([prompt], sampling)[0].outputs[0].token_ids
            assert before
            for _ in range(2):
                llm.sleep(level=1)
                llm.wake_up()
                after = llm.generate([prompt], sampling)[0].outputs[0].token_ids
                assert before == after, (before, after)
            pipe.send({"tokens": before})
            pipe.close()
            os._exit(0)
        if action == "mempool":
            import torch
            from gpu_memory_service.v1.client.mempool import TorchMempoolMemoryClient

            torch.cuda.set_device(device)
            client = TorchMempoolMemoryClient()
            with client.weight_region():
                model = torch.nn.Linear(8, 4, bias=False, device=f"cuda:{device}")
                with torch.no_grad():
                    model.weight.fill_(0.25)
            with client.kv_cache_region():
                kv = torch.ones(1024, device=f"cuda:{device}")
            client.publish_weights((model,))
            pointers = [p.data_ptr() for p in model.parameters()]
            x = torch.ones(1, 8, device=f"cuda:{device}")
            expected_output = model(x).detach().cpu()
            for _ in range(3):
                client.suspend()
                client.resume()
                assert pointers == [p.data_ptr() for p in model.parameters()]
                assert torch.equal(model(x).cpu(), expected_output)
                kv.fill_(1)
                assert kv.sum().item() == 1024
            pipe.send("mempool_recovery_passed")
            pipe.close()
            os._exit(0)
        expected = bytes((i * 37 + 19) % 251 for i in range(4096))
        if action in ("publish", "abort"):
            manager = GMSClientMemoryManager(path, vmm, device, slab_size=4096)
            manager.connect(RequestedLockType.RW)
            va = manager.create_mapping(4096)
            _bytes(vmm, va, expected)
            if action == "publish":
                manager.commit()
                assert _bytes(vmm, va) == expected
            mapping = manager.mappings[0]
            pipe.send((mapping.allocation_id, mapping.aligned_size))
            pipe.close()
            # Simulate client death without orderly handle/lease cleanup.
            os._exit(0)
        with closing(
            _GMSClientSession(path, RequestedLockType.RO, admission_timeout=2)
        ) as session:
            fd = session.export(record[0])
            granularity = vmm.get_allocation_granularity(device)
            mapping, handle = reserve_and_install_mapping(
                vmm,
                fd,
                record[0],
                4096,
                record[1],
                record[1],
                granularity,
                device,
                GrantedLockType.RO,
            )
            try:
                assert _bytes(vmm, mapping.base) == expected
                vmm.validate_pointer(mapping.base)
            finally:
                unmap_mapping(vmm, mapping, handle)
                vmm.address_free(mapping.base, mapping.reservation_size)
        pipe.send("readback_passed")
    except BaseException as error:
        pipe.send({"error": repr(error)})
        raise
    finally:
        pipe.close()


def _run_client(path, action, record=None):
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_client, args=(path, action, record, send))
    process.start()
    send.close()
    try:
        assert receive.poll(
            480 if action == "engine" else 60
        ), "client deadline exceeded"
        result = receive.recv()
        process.join(10)
        assert process.exitcode == 0, result
        assert not (isinstance(result, dict) and "error" in result), result
        return result
    finally:
        receive.close()
        if process.is_alive():
            process.terminate()
            process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)


@contextmanager
def _server(path):
    from gpu_memory_service.v1.device import get_device_uuid
    from gpu_memory_service.v1.server.rpc import GMSRPCServer, GMSServerMemoryManager

    vmm, device = _initialize()
    manager = GMSServerMemoryManager(get_device_uuid(device), vmm, device)
    server = GMSRPCServer(path, manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield manager
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        manager._clear_allocations()


@pytest.mark.timeout(180)
def test_committed_memory_survives_client_death(tmp_path):
    path = str(tmp_path / "gms.sock")
    with _server(path):
        record = _run_client(path, "publish")
        assert _run_client(path, "read", record) == "readback_passed"
        assert _run_client(path, "read", record) == "readback_passed"


@pytest.mark.timeout(180)
def test_uncommitted_client_death_allows_new_writer(tmp_path):
    path = str(tmp_path / "gms.sock")
    with _server(path):
        abandoned = _run_client(path, "abort")
        record = _run_client(path, "publish")
        assert abandoned[0] != record[0]
        assert _run_client(path, "read", record) == "readback_passed"


@pytest.mark.timeout(180)
def test_torch_mempool_preserves_weights_and_addresses(tmp_path, monkeypatch):
    from gpu_memory_service.v1.device import get_socket_path

    monkeypatch.setenv("GMS_SOCKET_DIR", str(tmp_path))
    _, device = _initialize()
    with ExitStack() as stack:
        for domain in ("weights", "kv_cache"):
            stack.enter_context(_server(get_socket_path(device, domain)))
        assert _run_client(str(tmp_path), "mempool") == "mempool_recovery_passed"


@pytest.mark.timeout(600)
def test_vllm_sleep_recovery(tmp_path, monkeypatch):
    from gpu_memory_service.v1.device import get_socket_path

    if not os.environ.get("M07_MODEL"):
        pytest.skip("set M07_MODEL to an existing local model for engine validation")
    monkeypatch.setenv("GMS_SOCKET_DIR", str(tmp_path))
    monkeypatch.setenv("VLLM_ENABLE_V1_MULTIPROCESSING", "0")
    _, device = _initialize()
    # vLLM TP1 uses logical device 0. Expose one physical device in the runner.
    assert device == 0, "engine validation requires M07_DEVICE=0 and one visible GPU"
    with ExitStack() as stack:
        for domain in ("weights", "kv_cache"):
            stack.enter_context(_server(get_socket_path(device, domain)))
        result = _run_client(str(tmp_path), "engine")
        assert result["tokens"]
