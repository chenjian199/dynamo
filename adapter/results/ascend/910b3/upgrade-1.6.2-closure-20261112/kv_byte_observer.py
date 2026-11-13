# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in TP1 test observer: compare actual KV bytes around native Mooncake READ.

The side channel copies source bytes solely for independent auditing. It never
populates decoder memory. Native transfer arguments/results are returned unchanged.
Do not use these instrumented runs as transport throughput measurements.
"""
import functools
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import threading
import time

ROOT = Path(os.environ['CLOSURE_KV_AUDIT_DIR'])
ROLE = os.environ['CLOSURE_KV_AUDIT_ROLE']
PORT = int(os.environ.get('CLOSURE_KV_AUDIT_PORT', '24190'))
MAX_BYTES = 64 * 1024 * 1024
ROOT.mkdir(parents=True, exist_ok=True)
_regions = []
_device = None
_lock = threading.Lock()
_counter = 0


def validate_ranges(addresses, sizes, regions):
    if not addresses or len(addresses) != len(sizes):
        raise ValueError('Empty or mismatched KV byte ranges')
    if sum(sizes) > MAX_BYTES:
        raise ValueError('Audit byte budget exceeded')
    for address, size in zip(addresses, sizes):
        if type(address) is not int or type(size) is not int or size <= 0:
            raise ValueError('Invalid KV address or byte count')
        if not any(start <= address and address + size <= start + length for start, length in regions):
            raise ValueError('KV range is outside registered model cache storage')


def receive(sock, length):
    chunks = []
    while length:
        chunk = sock.recv(min(length, 1024 * 1024))
        if not chunk:
            raise EOFError('Audit side channel closed early')
        chunks.append(chunk)
        length -= len(chunk)
    return b''.join(chunks)


def device_bytes(addresses, sizes):
    import acl
    import torch
    import torch_npu  # noqa: F401
    validate_ranges(addresses, sizes, _regions)
    torch.npu.set_device(_device)
    torch.npu.synchronize()
    chunks = []
    for address, size in zip(addresses, sizes):
        pointer, code = acl.rt.malloc_host(size)
        if code:
            raise RuntimeError(f'acl malloc_host failed: {code}')
        try:
            code = acl.rt.memcpy(pointer, size, address, size, 2)
            if code:
                raise RuntimeError(f'acl device-to-host copy failed: {code}')
            chunks.append(acl.util.ptr_to_bytes(pointer, size))
        finally:
            code = acl.rt.free_host(pointer)
            if code:
                raise RuntimeError(f'acl free_host failed: {code}')
    return b''.join(chunks)


class SourceReader(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(30)
        try:
            length = struct.unpack('!I', receive(self.request, 4))[0]
            if length > 65536:
                raise ValueError('Oversized audit descriptor')
            request = json.loads(receive(self.request, length))
            if request['nonce'] != os.environ['CLOSURE_KV_AUDIT_NONCE']:
                raise ValueError('Wrong audit run nonce')
            payload = device_bytes(request['addresses'], request['sizes'])
            header = json.dumps({'ok': True, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}).encode()
            self.request.sendall(struct.pack('!I', len(header)) + header + payload)
        except Exception as error:
            header = json.dumps({'ok': False, 'error': repr(error)}).encode()
            try:
                self.request.sendall(struct.pack('!I', len(header)) + header)
            except OSError:
                pass


def start_observer(worker, caches):
    global _regions, _device
    import torch
    if worker.tp_size != 1:
        raise ValueError('This byte observer is deliberately restricted to TP1')
    _device = torch.npu.current_device()
    regions = set()
    for cache in caches.values():
        values = (cache,) if isinstance(cache, torch.Tensor) else cache
        for tensor in values:
            storage = tensor.untyped_storage()
            regions.add((storage.data_ptr(), storage.nbytes()))
    _regions = sorted(regions)
    (ROOT / f'registered-{os.getpid()}.json').write_text(json.dumps({
        'role': ROLE, 'device': _device, 'regions': _regions,
        'groups': worker.kv_group2layeridx, 'pid': os.getpid(),
    }, indent=2)+'\n')
    if ROLE == 'prefill':
        server = socketserver.TCPServer(('0.0.0.0', PORT), SourceReader)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (ROOT / 'source-ready.json').write_text(json.dumps({'pid': os.getpid(), 'port': PORT})+'\n')


def install():
    from vllm_ascend.distributed.kv_transfer.kv_p2p.mooncake_connector import MooncakeConnectorWorker
    from mooncake.engine import TransferEngine
    register = MooncakeConnectorWorker.register_kv_caches

    @functools.wraps(register)
    def registered(self, caches):
        result = register(self, caches)
        start_observer(self, caches)
        return result

    MooncakeConnectorWorker.register_kv_caches = registered
    original = TransferEngine.batch_transfer_sync_read

    @functools.wraps(original)
    def transfer(self, session, local_addresses, remote_addresses, sizes):
        global _counter
        if ROLE != 'decode' or not (ROOT / 'armed').exists():
            return original(self, session, local_addresses, remote_addresses, sizes)
        with _lock:
            number = _counter
            _counter += 1
        if number >= 16:
            raise RuntimeError('Bounded KV audit exceeded 16 transfers')
        addresses = list(map(int, remote_addresses)); lengths = list(map(int, sizes))
        validate_ranges(list(map(int, local_addresses)), lengths, _regions)
        record = {'sequence': number, 'session': session, 'local_addresses': list(local_addresses),
                  'remote_addresses': addresses, 'sizes': lengths, 'started': time.monotonic(), 'passed': False, 'native_api': original.__name__}
        prefix = ROOT / f'transfer-{os.getpid()}-{number}'
        try:
            request = json.dumps({'nonce': os.environ['CLOSURE_KV_AUDIT_NONCE'], 'addresses': addresses, 'sizes': lengths}).encode()
            peer = (ROOT / 'peer').read_text().strip()
            with socket.create_connection((peer, PORT), timeout=30) as sock:
                sock.settimeout(30)
                sock.sendall(struct.pack('!I',len(request))+request)
                n = struct.unpack('!I',receive(sock,4))[0]
                if n > 65536:raise ValueError('Oversized audit reply')
                header = json.loads(receive(sock,n))
                if not header['ok']:raise RuntimeError(header['error'])
                if header['size'] != sum(lengths):raise ValueError('Audit byte count differs')
                producer = receive(sock,header['size'])
                if hashlib.sha256(producer).hexdigest() != header['sha256']:raise ValueError('Audit side-channel corruption')
            prefix.with_suffix('.producer.bin').write_bytes(producer)
            # This is the original vendor READ; no audit bytes are copied to NPU.
            result = original(self,session,local_addresses,remote_addresses,sizes)
            record['native_returncode'] = result
            if result < 0:return result
            consumer = device_bytes(list(map(int,local_addresses)),lengths)
            prefix.with_suffix('.consumer.bin').write_bytes(consumer)
            record.update(producer_sha256=hashlib.sha256(producer).hexdigest(),
                          consumer_sha256=hashlib.sha256(consumer).hexdigest(),
                          transferred_bytes=sum(lengths), passed=producer==consumer)
            if producer != consumer:raise AssertionError('Actual model KV bytes differ after native READ')
            return result
        except Exception as error:
            record['error'] = repr(error)
            raise
        finally:
            record['finished'] = time.monotonic()
            prefix.with_suffix('.json').write_text(json.dumps(record,indent=2)+'\n')

    TransferEngine.batch_transfer_sync_read = transfer


install()


class KVByteObserver:
    """Loaded explicitly by --worker-extension-cls for dedicated test workers."""
