"""Safe startup regression: no secret values read or printed; temporary files only."""
import json
import sys
import tempfile
from kernel_sandbox import run

CODE = '''
import os,socket,subprocess,json
from pathlib import Path
from docx import Document
import pandas as pd
results={}
results['nonroot']=os.getuid()!=0
results['clean_env']=all(k not in os.environ for k in ['NVIDIA_API_KEY','OPENROUTER_API_KEY','DATABASE_URL','AGENT_PASSWORD'])
for p in ['/app/app.py','/app/providers.json','/etc/passwd','/proc/1/environ']:
    try:
        with open(p,'rb') as f: f.read(1)
        results['read_denied:'+p]=False
    except OSError: results['read_denied:'+p]=True
try:
    with open('/app/outside-test','w') as f: f.write('test')
    results['outside_write_denied']=False
except OSError: results['outside_write_denied']=True
try:
    socket.socket()
    results['network_denied']=False
except OSError: results['network_denied']=True
results['nested_namespace_denied']=subprocess.run(['unshare','-Ur','true'],capture_output=True).returncode!=0
try:
    os.kill(os.getppid(),0)
    results['host_signal_denied']=False
except OSError: results['host_signal_denied']=True
try:
    os.setsid()
    results['detach_denied']=False
except OSError: results['detach_denied']=True
try:
    open('/etc/passwd','w').close()
    results['truncate_denied']=False
except OSError: results['truncate_denied']=True
Path('escape').symlink_to('/etc/passwd')
try:
    Path('escape').read_bytes()
    results['symlink_denied']=False
except OSError: results['symlink_denied']=True
d=Document();d.add_paragraph('Kernel sandbox startup test');d.save('test.docx')
results['docx_created']=Path('test.docx').stat().st_size>0
results['pandas_ok']=bool(pd.DataFrame({'x':[1,2]}).x.sum()==3)
print(json.dumps(results,sort_keys=True))
'''

def startup_test():
    with tempfile.TemporaryDirectory(prefix='sandbox-check-') as workspace:
        result=run([sys.executable,'-c',CODE],workspace,sys.prefix,20)
        print('[sandbox] regression:',json.dumps(result,sort_keys=True),flush=True)
        if result['status']!='success':
            raise RuntimeError('Kernel sandbox startup regression failed')
        checks=json.loads(result['output'])
        if not all(checks.values()):
            raise RuntimeError('Kernel sandbox startup restrictions failed')

        # Exercise the approved-install policy without installing packages or sending data.
        network_result=run([sys.executable,'-c',"import socket;from pathlib import Path;socket.socket().close();print('DNS_CONFIG_OK' if Path('/etc/resolv.conf').read_bytes() else 'DNS_CONFIG_EMPTY')"],workspace,sys.prefix,10,network=True)
        print('[sandbox] approved-network policy:',json.dumps(network_result,sort_keys=True),flush=True)
        if network_result['status']!='success' or 'DNS_CONFIG_OK' not in network_result['output']:
            raise RuntimeError('Approved-install network policy failed')
