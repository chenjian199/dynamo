#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from http.server import HTTPServer,BaseHTTPRequestHandler
from pathlib import Path
import shutil
archive=Path('/root/CJ_workspace/dynamo-huawei-cleanup-20261112/extended-cleanup/residual-source-and-evidence.tar.gz')
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.client_address[0]!='192.168.7.7' or self.path!='/011bf933cad74b61a14144a70fc01210':
   self.send_error(403);return
  self.connection.settimeout(120)
  self.send_response(200);self.send_header('Content-Length',str(archive.stat().st_size));self.end_headers()
  with archive.open('rb') as f:shutil.copyfileobj(f,self.wfile,4*1024*1024)
httpd=HTTPServer(('192.168.7.5',18965),Handler);httpd.timeout=60
httpd.handle_request();httpd.server_close()
