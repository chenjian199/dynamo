import json,sys,subprocess,shutil
from pathlib import Path
sys.path.insert(0,'/home/metax/CJ_workspace/dynamo')
from adapter.workflow.core import ROOT,atomic,digest
root=ROOT/'adapter/results/workflow/cpu-controls/20261105-offline-roundtrip-r3'
root.mkdir(parents=True)
contents=root/'contents';contents.mkdir()
base='docker.m.daocloud.io/library/registry:2'
roles=['runtime','dev','tools','operator'];refs=['heyi/adapter-workflow-fixture-'+r+':20261105' for r in roles]
tools=ROOT/'adapter/modules/m10-validation/common/offline'
commands=[]
def run(argv,name):
 p=subprocess.run(argv,capture_output=True,check=False)
 (root/(name+'.log')).write_bytes(p.stdout+p.stderr)
 commands.append({'argv':argv,'returncode':p.returncode,'log':name+'.log','sha256':digest(root/(name+'.log'))})
 if p.returncode:raise RuntimeError(name+' failed')
 return p.stdout
try:
 baseline=run(['docker','run','--rm','--network','none','--entrypoint','/bin/registry',base,'--version'],'baseline')
 for i,ref in enumerate(refs):run(['docker','tag',base,ref],'tag-'+str(i))
 run([sys.executable,str(tools/'export-images.py'),'--output',str(contents),*refs],'export')
 exported=json.loads((contents/'image-export.json').read_text())
 dependencies=contents/'dependencies/images';dependencies.mkdir(parents=True)
 with (dependencies/'control.tar').open('wb') as out:
  subprocess.run(['docker','save','--platform','linux/amd64',refs[0]],stdout=out,check=True)
 run([sys.executable,str(tools/'dependency-inventory.py'),'--directory',str(dependencies),'--arch','amd64','--output',str(contents/'dependencies.json')],'dependency-inventory')
 shutil.copy2(tools/'import.sh',contents/'import.sh')
 (contents/'SHA256SUMS').write_text(''.join(digest(p)+'  '+str(p.relative_to(contents))+'\n' for p in sorted(contents.rglob('*')) if p.is_file() and p.name!='SHA256SUMS'))
 for i,ref in enumerate(refs):run(['docker','image','rm',ref],'untag-'+str(i))
 run(['bash',str(contents/'import.sh'),'docker'],'import')
 actual=json.loads(run(['docker','image','inspect',*refs],'inspect-import'))
 assert [a['Id'] for a in actual]==[e['id'] for e in exported['images']]
 output=run(['docker','run','--rm','--network','none','--entrypoint','/bin/registry',refs[0],'--version'],'after-import')
 assert output==baseline and output
 (contents/'roundtrip-output.txt').write_bytes(output)
 report={'platform':'workflow-fixture','architecture':'amd64','fixture_only':True,'note':'Four aliases of a tiny registry image test archive mechanics only. Not Dynamo images, inference, or platform qualification.',
         'checks':[{'name':'real image archive export/import and command byte parity','status':'passed','evidence':{'roundtrip-output.txt':digest(contents/'roundtrip-output.txt')}}],
         'images':[{'role':role,**entry} for role,entry in zip(roles,exported['images'])]}
 atomic(contents/'validation.json',report)
 run([sys.executable,str(tools/'bundle.py'),'--directory',str(contents),'--archive',str(root/'workflow-fixture.tar'),'--prepared-export'],'seal')
 run([sys.executable,str(tools/'bundle.py'),'--directory',str(contents),'--archive',str(root/'unused.tar'),'--verify-only'],'reverify')
 atomic(root/'summary.json',{'fixture_only':True,'architecture':'amd64','imported_ids_match':True,'command_bytes_match':True,'archive_sha256':digest(root/'workflow-fixture.tar'),'commands':commands})
 print('Real archive roundtrip and sealing passed (fixture images only)')
finally:
 for i,ref in enumerate(refs):run(['docker','image','rm',ref],'cleanup-'+str(i))
