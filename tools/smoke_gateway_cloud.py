"""Explicit SIMULATED terminal-only cloud smoke; never drives external hardware."""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import aiohttp
from gateway.runtime import Gateway,utc
from gateway.adapters import TerminalAdapter
from gateway.nameplates import NameplateClient


async def smoke(config,access_code,nameplates=False):
    if config['mode']!='SIMULATED' or config['adapter']['kind']!='terminal':
        raise ValueError('Smoke requires explicit SIMULATED terminal configuration')
    gateway=Gateway(config,os.environ[config['token_env']],TerminalAdapter())
    task=asyncio.create_task(gateway.run())
    base=config['hub_url'].rstrip('/')
    sid=None
    device_client=NameplateClient(gateway)
    grant_id=None
    started=time.monotonic()
    try:
        async with aiohttp.ClientSession(headers={'User-Agent':'SUMMON-Gateway-Smoke/0.1','Origin':base},trust_env=True,timeout=aiohttp.ClientTimeout(total=12)) as http:
            async def call(method,path,body=None):
                async with http.request(method,base+path,json=body) as response:
                    if response.status>=400:
                        raise RuntimeError('HTTP '+str(response.status)+' at '+path)
                    return await response.json()
            await call('POST','/v1/operator-session',{'access_code':access_code})
            for _ in range(30):
                if task.done():task.result()
                catalog=await call('GET','/v1/catalog')
                shell=next(s for s in catalog['shells'] if s['shell_id']==config['shell_id'])
                if shell['state']=='IDLE':break
                await asyncio.sleep(.3)
            else:raise RuntimeError('Terminal shell did not become IDLE')
            agent=next(a for a in catalog['agents'] if a['status']=='ONLINE' and 'display.text' in a['capabilities'])
            if nameplates:
                await device_client.refresh()
                plate=next(p for p in device_client.directory if p['agent']['agent_id']==agent['agent_id'])
                pair=await device_client.call('POST','/v1/gateway/pairings',{})
                preview=await call('POST','/v1/device-pairings/preview',{'request_id':'preview_'+str(time.time_ns()),'user_code':pair['user_code']})
                approved=await call('POST','/v1/device-pairings/approve',{'request_id':'approve_'+str(time.time_ns()),'user_code':pair['user_code'],
                    'shell_id':preview['shell_id'],'capabilities':['display.text']})
                grant_id=approved['grant_id']
                device_client.grant=await device_client.call('POST','/v1/gateway/pairings/'+pair['pairing_id']+'/claim',{})
                await device_client.lookup(plate['code'])
                await device_client.connect()
                sid=device_client.session['session_id']
            else:
                opened=await call('POST','/v1/sessions',{'request_id':'gateway_open_'+str(time.time_ns()),'agent_id':agent['agent_id'],'shell_id':config['shell_id']})
                sid=opened['session']['session_id']
            for _ in range(20):
                if task.done():task.result()
                state=await call('GET','/v1/state')
                if next(s for s in state['sessions'] if s['session_id']==sid)['state']=='ACTIVE':break
                await asyncio.sleep(.1)
            else:raise RuntimeError('Session activation timed out')
            if nameplates:
                await device_client.status()
                await device_client.submit('Nameplate cloud verification: print this on the local computer.')
            else:
                await call('POST','/v1/sessions/'+sid+'/inputs',{'request_id':'gateway_input_'+str(time.time_ns()),'text':'Gateway cloud verification: print this on the local computer.'})
            for _ in range(25):
                if task.done():task.result()
                state=await call('GET','/v1/state')
                records=[c for c in state['commands'] if c['request']['session_id']==sid]
                if records and records[-1]['outcome']['status'] in ('COMPLETED','FAILED','UNKNOWN'):break
                await asyncio.sleep(.2)
            if not records or records[-1]['outcome']['status']!='COMPLETED':
                raise RuntimeError('Terminal result was not COMPLETED')
            cid=records[-1]['request']['command_id']
            evidence=await call('GET','/v1/experiences?shell_id='+config['shell_id'])
            item=next(e for e in evidence['items'] if e['command_id']==cid)
            assert item['mode']=='SIMULATED' and item['status']=='COMPLETED'
            for _ in range(30):
                if not gateway.journal.pending():break
                await asyncio.sleep(.1)
            assert not gateway.journal.pending(),'Cloud upload acknowledgement missing'
            result={'at':utc(),'mode':'SIMULATED','adapter':'terminal','physical_hardware_tested':False,
                    'agent':'remote rule-based demo agent','gateway':'local computer','cloud_evidence_saved':True,
                    'upload_acknowledged':True,'command_id':cid,'session_id':sid,'profile':item['profile'],
                    'scenario_total_ms':round((time.monotonic()-started)*1000),'note':'Scenario includes HTTPS polling and setup; not action round-trip latency.'}
            if nameplates:
                result['nameplate']=plate['code']
                result['device_pairing_verified']=True
                await device_client.release()
                await call('POST','/v1/device-authorizations/'+grant_id+'/revoke',{'request_id':'revoke_'+str(time.time_ns())})
                grant_id=None
                result['release_and_revoke_verified']=True
            return result
    finally:
        if sid:
            try:
                async with aiohttp.ClientSession(headers={'User-Agent':'SUMMON-Gateway-Smoke/0.1','Origin':base},trust_env=True,timeout=aiohttp.ClientTimeout(total=10)) as cleanup:
                    async with cleanup.post(base+'/v1/operator-session',json={'access_code':access_code}) as r:
                        await r.read()
                    async with cleanup.post(base+'/v1/sessions/'+sid+'/release',json={'request_id':'gateway_release_'+str(time.time_ns())}) as r:
                        await r.read()
                    if grant_id:
                        async with cleanup.post(base+'/v1/device-authorizations/'+grant_id+'/revoke',json={'request_id':'cleanup_revoke_'+str(time.time_ns())}) as r:
                            await r.read()
                    await asyncio.sleep(1)
            except (aiohttp.ClientError,asyncio.TimeoutError):
                pass
        task.cancel();await asyncio.gather(task,return_exceptions=True);gateway.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--nameplates',action='store_true')
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding='utf-8'))
    config['database']=str((args.config.resolve().parent/config['database']).resolve())
    result=asyncio.run(smoke(config,os.environ['SUMMON_OPERATOR_CODE'],args.nameplates))
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('PASS: local terminal execution and acknowledged cloud experience upload')


if __name__=='__main__':
    main()
