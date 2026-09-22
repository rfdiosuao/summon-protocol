"""Exercise the deployed SIMULATED platform, without logging credentials."""
import asyncio
import json
import os
from pathlib import Path
import secrets

import aiohttp


async def main():
    cfg=json.loads(Path(os.environ['SUMMON_CONFIG']).read_text())
    base=cfg['origin']
    code=next(iter(cfg['operator_codes']))
    async with aiohttp.ClientSession(headers={'Origin':base},timeout=aiohttp.ClientTimeout(total=15)) as client:
        async def call(path,body=None):
            async with client.request('POST' if body is not None else 'GET',base+path,json=body) as r:
                data=await r.json()
                if r.status>=400:
                    raise RuntimeError(str(r.status)+' '+data.get('error',{}).get('code','unknown'))
                return data
        health=await call('/healthz')
        assert health['mode']=='SIMULATED','Smoke test refuses to control LIVE devices'
        await call('/v1/operator-session',{'access_code':code})
        state=await call('/v1/state')
        assert len(state['shells'])==2 and all(s['state']=='IDLE' for s in state['shells'])
        aid=next(a['agent_id'] for a in state['agents'] if a['status']=='ONLINE')
        rid=lambda:secrets.token_hex(12)
        session=await call('/v1/sessions',{'request_id':rid(),'agent_id':aid,'shell_id':'shell_a'})
        sid=session['session']['session_id']
        async def wait(predicate):
            for _ in range(60):
                result=await call('/v1/state')
                if predicate(result): return result
                await asyncio.sleep(.2)
            raise RuntimeError('Expected state did not arrive')
        await wait(lambda x:any(s['session_id']==sid and s['state']=='ACTIVE' for s in x['sessions']))
        history_before=await call('/v1/sessions/'+sid+'/experiences?capability=display.text')
        memory=await call('/v1/sessions/'+sid+'/memory')
        saved=await call('/v1/sessions/'+sid+'/feedback',{'request_id':rid(),'update_id':rid(),'expected_version':memory['memory_version'],'patch':{'response_style':'brief'}})
        await call('/v1/sessions/'+sid+'/inputs',{'request_id':rid(),'text':'请介绍一下展品'})
        result=await wait(lambda x:any(c['request']['session_id']==sid and c['outcome']['status']=='COMPLETED' for c in x['commands']))
        history=await call('/v1/sessions/'+sid+'/experiences?capability=display.text')
        assert history['total']==history_before['total']+1
        assert '请介绍一下展品' not in json.dumps(history,ensure_ascii=False)
        await asyncio.sleep(.2)
        await call('/v1/sessions/'+sid+'/inputs',{'request_id':rid(),'text':'再介绍一次'})
        result=await wait(lambda x:sum(c['request']['session_id']==sid and c['outcome']['status']=='COMPLETED' for c in x['commands'])==2)
        latest=[c for c in result['commands'] if c['request']['session_id']==sid][-1]
        assert '历史 '+str(history['total'])+' 条' in latest['outcome']['result']
        after=await call('/v1/sessions/'+sid+'/experiences?capability=display.text')
        assert after['total']==history['total']+1
        await call('/v1/sessions/'+sid+'/handoff',{'request_id':rid(),'target_shell_id':'shell_b'})
        result=await wait(lambda x:any(s['shell_id']=='shell_b' and s['state']=='ACTIVE' for s in x['sessions']))
        target=next(s for s in result['sessions'] if s['shell_id']=='shell_b' and s['state']=='ACTIVE')
        assert target['memory_version']==saved['memory_version']
        target_history=await call('/v1/sessions/'+target['session_id']+'/experiences')
        assert all(i['shell_id']=='shell_b' for i in target_history['items'])
        assert next(s for s in result['sessions'] if s['session_id']==sid)['state']=='RELEASED'
        await call('/v1/sessions/'+target['session_id']+'/release',{'request_id':rid()})
        await wait(lambda x:all(s['state']=='IDLE' for s in x['shells']))
        print('PASS HTTPS login, experience persistence and retrieval before second action, shell isolation, memory, handoff, release')


if __name__=='__main__':
    asyncio.run(main())
