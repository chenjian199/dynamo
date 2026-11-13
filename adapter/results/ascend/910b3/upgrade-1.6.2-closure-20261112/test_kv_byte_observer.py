# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounds and framing checks only; no claim of NPU validation."""
import ast
from pathlib import Path
import socket
import unittest

p=Path(__file__).with_name('kv_byte_observer.py')
tree=ast.parse(p.read_text())
selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('validate_ranges','receive')]
ns={'MAX_BYTES':64*1024*1024}
exec(compile(ast.Module(body=selected,type_ignores=[]),str(p),'exec'),ns)


class ByteObserverTests(unittest.TestCase):
    def test_registered_boundaries(self):
        ns['validate_ranges']([100,130],[10,10],[(100,40)])
        for addresses,sizes in [([99],[1]),([139],[2]),([100],[0]),([100],[-1]),([100,110],[1]),([] ,[]),([100.0],[1])]:
            with self.subTest(addresses=addresses,sizes=sizes),self.assertRaises(ValueError):
                ns['validate_ranges'](addresses,sizes,[(100,40)])

    def test_budget(self):
        with self.assertRaises(ValueError):
            ns['validate_ranges']([100],[ns['MAX_BYTES']+1],[(100,ns['MAX_BYTES']+1)])

    def test_exact_frame_and_early_disconnect(self):
        a,b=socket.socketpair()
        try:
            b.sendall(b'abcdef');b.shutdown(socket.SHUT_WR)
            self.assertEqual(ns['receive'](a,3),b'abc')
            self.assertEqual(ns['receive'](a,3),b'def')
            with self.assertRaises(EOFError):ns['receive'](a,1)
        finally:a.close();b.close()


if __name__=='__main__':unittest.main()
