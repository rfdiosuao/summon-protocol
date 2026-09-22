"""Remote Passport Agent. Optional model; explicit limited intent mode without one."""
import asyncio
import json
import os
from pathlib import Path
import re
import time

import aiohttp
from hub.app import uid,utc,stamp


def builtin_plan(text):
    if any(word in text for word in ('浏览器','打开官网','打开网页')):
        return {'url':'https://summon.entermodetwo.com/','reply':'已打开电脑上的 SUMMON 官网。'}
    if any(word in text for word in ('时间','几点','日期')):
        return {'command':"Get-Date -Format 'yyyy-MM-dd HH:mm:ss'",'reply':'电脑当前时间'}
    if any(word in text for word in ('计算机名','电脑名字','电脑名称')):
        return {'command':'$env:COMPUTERNAME','reply':'电脑名称'}
    return {'reply':'当前是有限指令模式。可以说：打开浏览器、查看当前时间、查看电脑名称。通用对话需要配置模型服务。'}


async def run(config_path,credentials_path):
    cfg=json.loads(Path(config_path).read_text(encoding='utf-8'))
    base=cfg.get('hub_url','http://127.0.0.1:8840').rstrip('/')
    path=Path(credentials_path)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),headers={'User-Agent':'SUMMON-Passport-Agent/0.1'}) as http:
        if path.exists():registered=json.loads(path.read_text())
        else:
            body={'request_id':'summon_passport_voice_agent_v1','name':'唤名 · Passport 语音 Agent',
                  'bio':'Passport 语音入口；未配置模型时仅支持公开列出的有限指令。',
                  'capabilities':['display.text','browser.open','command.exec']}
            async with http.post(base+'/v1/agents',headers={'Authorization':'Bearer '+cfg['invite']},json=body) as r:
                if r.status!=201:raise RuntimeError('Registration HTTP '+str(r.status))
                registered=await r.json()
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(registered,f)
        auth={'Authorization':'Bearer '+registered['agent_token']}
        async with http.get(base+'/v1/agents/me/nameplate',headers=auth) as r:
            r.raise_for_status();plate=await r.json()
            if plate['agent']['agent_id']!=registered['agent']['agent_id']:raise RuntimeError('Identity mismatch')
            print('Passport Agent nameplate:',plate['code'],'mode:','model' if cfg.get('model') else 'limited-intents',flush=True)
        delay=1
        while True:
            sessions={};pending={};jobs={};sequences={}
            try:
                async with http.ws_connect(base+'/v1/connect',headers=auth) as ws:
                    async def send(kind,p):
                        await ws.send_json({'v':1,'message_id':uid('passportmsg'),'sent_at':utc(),'type':kind,'payload':p})
                    async def action(session,input_id,capability,args):
                        sid=session['session_id']
                        if not session.get('active') or capability not in session['permitted_capabilities']:raise RuntimeError('Capability unavailable')
                        if stamp(session['expires_at'])-time.time()<12:raise RuntimeError('Lease nearly expired')
                        cid=uid('passport');sequences[sid]+=1
                        future=asyncio.get_running_loop().create_future();pending[cid]=future
                        try:
                            await send('action.request',{'session_id':sid,'lease_epoch':session['lease_epoch'],
                                'input_id':input_id,'command_id':cid,'seq':sequences[sid],
                                'expires_at':utc(min(time.time()+4,stamp(session['expires_at']))),
                                'action':{'capability':capability,'args':args}})
                            return await asyncio.wait_for(future,12)
                        finally:pending.pop(cid,None)
                    async def process(p):
                        s=sessions[p['session_id']]
                        try:
                            plan=builtin_plan(p['text'])
                            if cfg.get('model'):
                                model=cfg['model']
                                prompt='你是 SUMMON Passport Agent。用户对实体设备说话，通过已授权电脑执行。只返回 JSON 对象，字段 reply（简短中文）、command（PowerShell 或 null）、url（HTTPS 或 null）。最多一种动作。查询电脑当前时间必须返回 command="Get-Date -Format o"；打开浏览器必须返回 url="https://summon.entermodetwo.com/"。不要用正在执行等空话代替实际动作。不要执行删除、购买、发送消息、安装或更改凭证；这些需用户在电脑确认。普通聊天不执行命令。不声称命令已执行。支持的能力：'+','.join(s['permitted_capabilities'])
                                async with http.post(model['base_url'].rstrip('/')+'/chat/completions',
                                    headers={'Authorization':'Bearer '+model['api_key']},
                                    json={'model':model['name'],'messages':[{'role':'system','content':prompt},{'role':'user','content':p['text']}],
                                          'max_tokens':500},timeout=aiohttp.ClientTimeout(total=25)) as r:
                                    if r.status!=200:raise RuntimeError('Model service unavailable')
                                    content=(await r.json())['choices'][0]['message']['content']
                                    plan=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',content.strip()))
                                # Stable tools for the explicitly supported demonstration intents.
                                # Model narration alone must never count as a computer operation.
                                deterministic=builtin_plan(p['text'])
                                if deterministic.get('command') or deterministic.get('url'):plan=deterministic
                            reply=str(plan.get('reply',''))[:350]
                            cap,args=None,None
                            if plan.get('command'):
                                command=plan['command']
                                if not isinstance(command,str) or not 1<=len(command)<=1000:raise ValueError('Invalid model command')
                                cap,args='command.exec',{'command':command}
                            elif plan.get('url'):cap,args='browser.open',{'url':plan['url']}
                            if cap:
                                outcome=await action(s,p['input_id'],cap,args)
                                if outcome['status']=='UNKNOWN':return
                                if outcome['status']!='COMPLETED':reply='电脑执行失败，请在电脑检查结果。'
                                elif cap=='command.exec':reply='电脑执行完成。'+outcome.get('execution',{}).get('stdout','')[:250]
                                else:reply='电脑已接受打开浏览器请求。'
                            await action(s,p['input_id'],'display.text',{'text':reply or '已收到。'})
                            if s.get('active'):await send('input.finished',{'session_id':s['session_id'],'input_id':p['input_id'],'status':'COMPLETED'})
                        except asyncio.CancelledError:raise
                        except Exception as exc:
                            print('Passport input failed:',type(exc).__name__,flush=True)
                            if s.get('active'):
                                try:await send('input.finished',{'session_id':s['session_id'],'input_id':p['input_id'],'status':'FAILED'})
                                except aiohttp.ClientError:pass
                    await send('hello',{'role':'agent','id':registered['agent']['agent_id']})
                    async for frame in ws:
                        if frame.type!=aiohttp.WSMsgType.TEXT:break
                        m=json.loads(frame.data);kind,p=m['type'],m['payload']
                        if kind=='heartbeat':await send('heartbeat',p)
                        elif kind=='session.offer':
                            s=p['session'];sessions[s['session_id']]=dict(s,active=False,permitted_capabilities=p['permitted_capabilities']);sequences[s['session_id']]=0
                            await send('session.ready',{'session_id':s['session_id'],'lease_epoch':s['lease_epoch'],'role':'agent'})
                        elif kind=='session.activate':sessions[p['session_id']]['active']=True
                        elif kind=='session.revoke':
                            sid=p['session_id']
                            if sid in sessions:sessions[sid]['active']=False
                            if sid in jobs:jobs[sid].cancel()
                        elif kind=='input.text':
                            sid=p['session_id']
                            if sessions.get(sid,{}).get('active') and (sid not in jobs or jobs[sid].done()):jobs[sid]=asyncio.create_task(process(p))
                        elif kind in ('action.completed','action.failed'):
                            f=pending.get(p['command_id'])
                            if f is not None and not f.done():f.set_result(p)
            except aiohttp.WSServerHandshakeError as exc:
                if exc.status in (401,403):raise RuntimeError('Agent credential rejected') from None
            except (aiohttp.ClientError,asyncio.TimeoutError):pass
            finally:
                for job in jobs.values():job.cancel()
                await asyncio.gather(*jobs.values(),return_exceptions=True)
            await asyncio.sleep(delay);delay=min(30,delay*2)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True);parser.add_argument('--credentials',required=True)
    args=parser.parse_args();asyncio.run(run(args.config,args.credentials))
