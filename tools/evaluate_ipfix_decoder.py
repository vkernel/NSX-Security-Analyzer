#!/usr/bin/env python3
"""Developer-only isolated synthetic GoFlow2 compatibility gate. No NSX requests."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

DOCKER = os.environ.get('DOCKER', 'docker')
IMAGE = 'netsampler/goflow2@sha256:b930e69d1bc7bd765da5b45d12f04edd10970c310b33ba44fd12a07a870da8e6'
SENDER = os.environ.get('IPFIX_TEST_IMAGE', 'nsx-security-analyzer:ipfix-review')
MAPPING = '''formatter:
  fields:
    - type
    - src_addr
    - dst_addr
    - src_port
    - dst_port
    - proto
    - synthetic_enterprise_value
  protobuf:
    - name: synthetic_enterprise_value
      index: 1001
      type: varint
ipfix:
  mapping:
    - field: 1
      destination: synthetic_enterprise_value
      penprovided: true
      pen: 6876
'''
# Enterprise element is synthetic, not a claimed VMware rule-ID definition.
SEND = '''import socket,struct,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
def send(setid,body,seq=0,domain=77):
 block=struct.pack('!HH',setid,len(body)+4)+body
 s.sendto(struct.pack('!HHIII',10,len(block)+16,int(time.time()),seq,domain)+block,('127.0.0.1',2055))
fields=[(8,4),(12,4),(7,2),(11,2),(4,1)]
template=struct.pack('!HH',300,6)+b''.join(struct.pack('!HH',*f) for f in fields)+struct.pack('!HHI',0x8001,4,6876)
send(2,template)
time.sleep(.3)
for seq in range(3):
 send(300,socket.inet_aton('192.0.2.1')+socket.inet_aton('198.51.100.2')+struct.pack('!HHBI',12345,443,6,123456),seq)
 time.sleep(.1)
'''


def run(*args, **kwargs):
    result = subprocess.run([DOCKER, *args], capture_output=True, text=True, timeout=90, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout + (result.stderr if args[0] == 'logs' else '')


def main():
    name = 'nsxa-decoder-evaluation-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='nsxa-decoder-') as folder:
        mapping = Path(folder) / 'mapping.yaml'
        mapping.write_text(MAPPING)
        try:
            run('run','-d','--name',name,'--network','none','--read-only','--cap-drop','ALL',
                '--memory','256m','--cpus','1','--log-opt','max-size=1m','--log-opt','max-file=1',
                '-v',str(mapping)+':/mapping.yaml:ro',IMAGE,
                '-listen','netflow://:2055','-mapping','/mapping.yaml','-format','json')
            time.sleep(1)
            run('run','--rm','-i','--network','container:'+name,'--read-only','--cap-drop','ALL',
                '--memory','128m',SENDER,'python','-',input=SEND)
            time.sleep(1)
            rows=[]
            for line in run('logs',name).splitlines():
                try: rows.append(json.loads(line))
                except ValueError: pass
            flows=[r for r in rows if r.get('synthetic_enterprise_value') == 123456]
            if len(flows) != 3 or any(r.get('src_addr')!='192.0.2.1' or r.get('dst_addr')!='198.51.100.2'
                                      or r.get('dst_port')!=443 for r in flows):
                raise RuntimeError('Decoder did not preserve synthetic fields: '+json.dumps(rows))
            print('PASS: 3 synthetic IPv4 records and enterprise values preserved. Actual NSX compatibility remains unverified.')
        except Exception:
            print(run('logs',name))
            raise
        finally:
            subprocess.run([DOCKER,'rm','-f',name],capture_output=True,timeout=30)


if __name__=='__main__':
    main()
