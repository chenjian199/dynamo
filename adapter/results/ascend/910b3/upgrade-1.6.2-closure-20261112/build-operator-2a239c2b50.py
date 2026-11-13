# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import hashlib,json,os,subprocess,time
from pathlib import Path
r=Path('/data/dynamo-upgrade-1.6.2-20261111');s=Path('/data/dynamo-upgrade-1.6.2-20261111/source-2a239c2b50');out=Path('/root/CJ_workspace/dynamo-upgrade-1.6.2-20261111/adapter/results/ascend/910b3/upgrade-1.6.2-closure-20261112/operator-2a239c2b50');out.mkdir();manifest=json.loads((s/'SOURCE.json').read_text());assert manifest['commit']=='2a239c2b500ea7e48c86b7216b9878158db4802a'
for p,h in manifest['files'].items():
 if p.startswith(('deploy/operator/','container/compliance/','deploy/helm/charts/platform/components/operator/')):assert hashlib.sha256((s/p).read_bytes()).hexdigest()==h,p
env={**os.environ,'PATH':'/data/dynamo-m10/docker-bin:'+os.environ['PATH'],'DOCKER_HOST':'unix:///run/dynamo-m10-docker.sock','DOCKER_CONFIG':str(r/'docker-client-direct'),'DOCKER_PROXY':'docker.m.daocloud.io/','BUILD_GOPROXY':'https://goproxy.cn,direct','BUILD_NETWORK':'host','OPERATOR_TAG_PREFIX':'1.6.2-heyi-upgrade-2a239c2b50'}
record={'source_commit':manifest['commit'],'started':time.time()}
with (out/'launch.log').open('x') as f:p=subprocess.run(['bash',str(s/'adapter/modules/m08-kubernetes/common/operator/build-operator.sh'),'arm64',str(out)],env=env,stdout=f,stderr=subprocess.STDOUT,timeout=1800)
record.update(exit_code=p.returncode,finished=time.time());(out/'result.json').write_text(json.dumps(record,indent=2));p.check_returncode()
