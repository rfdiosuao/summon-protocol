"""Single-writer SUMMON Hub. Run one worker; see docs/DEPLOYMENT.md."""
import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web, WSMsgType
from jsonschema import Draft202012Validator, FormatChecker
from hub.experience import ExperienceLedger

ROOT = Path(__file__).resolve().parents[1]


def uid(prefix='id'):
    return prefix+'_'+secrets.token_hex(12)


def utc(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Rejected(Exception):
    def __init__(self, code, status=409, message=None):
        self.code, self.status, self.message = code, status, message


def error(code, request_id='unknown', message=None):
    messages={'UNAUTHORIZED':'Authentication is required or the credential is invalid.',
              'FORBIDDEN':'This identity is not permitted to access this resource.',
              'NOT_FOUND':'The requested route or resource does not exist.',
              'INVALID_MESSAGE':'The method, message format or fields are invalid.',
              'SESSION_NOT_ACTIVE':'The session is not active; obtain a new authorization.',
              'CONNECT_TIMEOUT':'Both peers did not become ready within five seconds.',
              'UNKNOWN':'Execution could not be verified; do not replay the action.'}
    return {'error':{'code':code, 'message':message or messages.get(code,'Request rejected: '+code.lower().replace('_',' ')+'.'), 'retryable':False, 'request_id':request_id}}


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, key TEXT, value TEXT, PRIMARY KEY(kind,key))')

    def all(self, kind):
        return {k:json.loads(v) for k,v in self.db.execute('SELECT key,value FROM records WHERE kind=?',(kind,))}

    def save(self, **groups):
        with self.db:
            for kind, values in groups.items():
                for key, value in values.items():
                    self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',(kind,key,canonical(value)))


class Hub:
    def __init__(self, path, cfg):
        self.cfg, self.store = cfg, Store(path)
        self.agents = self.store.all('agents')
        self.sessions = self.store.all('sessions')
        self.commands = self.store.all('commands')
        self.gateway_receipts = self.store.all('gateway_receipts')
        self.experiences = ExperienceLedger(self.store)
        self.memories = self.store.all('memories')
        self.idem = self.store.all('idem')
        self.updates = self.store.all('updates')
        self.epochs = self.store.all('epochs')
        self.shells = {}
        self.connections, self.cookies, self.ready, self.inputs, self.moves = {}, {}, {}, {}, {}
        self.deadlines, self.limits = {}, {}
        self.stream, self.seq, self.events = uid('stream'), 0, deque()
        self.tasks = set()
        self.lock = asyncio.Lock()
        schema = json.loads((ROOT/'protocol/summon.schema.json').read_text(encoding='utf-8'))
        self.validators = {name:Draft202012Validator({'$defs':schema['$defs'], '$ref':'#/$defs/'+name}, format_checker=FormatChecker()) for name in schema['$defs']}
        for sid in cfg['gateway_tokens']:
            self.shells[sid] = {'shell_id':sid, 'label':cfg.get('shell_labels',{}).get(sid,sid), 'state':'OFFLINE',
                'capabilities':['display.text'], 'allowed_actions':['display.text'], 'enabled':False,
                'current_session_id':None, 'last_seen_at':None, 'identity_gates':['web','button'], 'stop_kind':'local_disable', 'gate':'whitelist'}
            policy=cfg.get('shell_policies',{}).get(sid,{})
            if not isinstance(policy,dict) or set(policy)-{'capabilities','allowed_actions','identity_gates','stop_kind','gate'}:
                raise ValueError('Invalid shell policy: '+sid)
            self.shells[sid].update(policy)
            self.validate('Shell',self.shells[sid])
            if not set(self.shells[sid]['allowed_actions']).issubset(self.shells[sid]['capabilities']):
                raise ValueError('allowed_actions must be implemented capabilities: '+sid)
        for agent in self.agents.values():
            agent['public']['status'] = 'OFFLINE'
        for sid, s in self.sessions.items():
            if s['state'] not in ('RELEASED','FAILED'):
                s['state'] = 'FAILED'
            # A failed session without a persisted stop remains quarantined after restart.
            if s['state']=='FAILED' and s['shell_id'] in self.shells:
                sh = self.shells[s['shell_id']]
                sh['current_session_id'], sh['state'] = sid, 'FAULT'
        for record in self.commands.values():
            if record['outcome']['status'] in ('ACCEPTED','EXECUTING'):
                q = record['request']
                record['outcome'] = {'session_id':q['session_id'],'command_id':q['command_id'],'status':'UNKNOWN','error':error('UNKNOWN',q['command_id'])}
        self.store.save(agents=self.agents,sessions=self.sessions,commands=self.commands)
        for record in self.commands.values():
            self.experiences.finish(record, 'recovery')
        from hub.nameplates import Nameplates
        self.nameplates=Nameplates(self)
        from hub.onboarding import Onboarding
        self.onboarding=Onboarding(self)
        from hub.agent_dashboard import AgentDashboard
        self.agent_dashboard=AgentDashboard(self)

    def validate(self, name, value):
        if not self.validators[name].is_valid(value):
            raise Rejected('INVALID_MESSAGE',400)

    def emit(self, kind, payload, owner=None):
        self.seq += 1
        event = {'v':1,'event_id':self.stream+':'+str(self.seq),'at':utc(),'mode':self.cfg['mode'],'type':kind,'payload':payload}
        self.validate('Event',event)
        self.events.append((time.monotonic(),owner,json.loads(canonical(event))))
        while self.events and self.events[0][0]<time.monotonic()-600:
            self.events.popleft()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def rate(self, key, maximum=20, seconds=60):
        now = time.monotonic()
        queue = self.limits.setdefault(key,deque())
        while queue and queue[0]<now-seconds:
            queue.popleft()
        if len(queue)>=maximum:
            raise Rejected('RATE_LIMITED',429)
        queue.append(now)

    def operator(self, request):
        cookie=request.cookies.get('summon_session','')
        entry = self.cookies.get(digest(cookie))
        if not cookie:
            raise Rejected('UNAUTHORIZED',401,'Missing operator session cookie; sign in with the access code.')
        if not entry:
            raise Rejected('UNAUTHORIZED',401,'Invalid operator session; sign in again (server restart also invalidates sessions).')
        if entry[1]<time.time():
            raise Rejected('UNAUTHORIZED',401,'Operator session expired; sign in again.')
        return entry[0]

    def bearer(self, request):
        value = request.headers.get('Authorization','')
        return value[7:] if value.startswith('Bearer ') else ''

    def identity(self, token):
        if not token:
            raise Rejected('UNAUTHORIZED',401,'Missing Authorization: Bearer header.')
        for aid,a in self.agents.items():
            if secrets.compare_digest(a['token_hash'],digest(token)):
                return ('agent',aid)
        for sid,value in self.cfg['gateway_tokens'].items():
            if secrets.compare_digest(token,value):
                return ('gateway',sid)
        raise Rejected('UNAUTHORIZED',401,'Invalid or revoked agent/gateway token; contact the deployment owner.')

    def reader(self, request, allow_agent=False):
        if allow_agent and request.headers.get('Authorization'):
            return self.identity(self.bearer(request))
        return ('operator',self.operator(request))

    def access(self, request, sid, allow_agent=False, require_active_agent=True):
        role,identifier=self.reader(request,allow_agent)
        s = self.sessions.get(sid)
        if not s:
            raise Rejected('NOT_FOUND',404)
        if role!='operator':
            if (role,identifier)==('agent',s['agent_id']) and (not require_active_agent or s['state']=='ACTIVE'):
                return s
            raise Rejected('FORBIDDEN',403)
        if s['operator_id']!=identifier:
            raise Rejected('FORBIDDEN',403)
        return s

    def memory(self, s):
        key = s['operator_id']+':'+s['agent_id']
        return self.memories.get(key, {'operator_id':s['operator_id'],'agent_id':s['agent_id'],'memory_version':0,'preferences':{'response_style':'detailed'},'updated_at':utc()})

    def update_memory(self, s, body):
        key = s['operator_id']+':'+s['agent_id']
        ukey = key+':'+body['update_id']
        data = {k:body[k] for k in ('expected_version','patch')}
        previous = self.updates.get(ukey)
        if previous:
            if previous['body']!=data:
                raise Rejected('IDEMPOTENCY_CONFLICT')
            return previous['result']
        self.active(s)
        current = self.memory(s)
        if current['memory_version']!=body['expected_version']:
            raise Rejected('MEMORY_CONFLICT')
        new = dict(current,memory_version=current['memory_version']+1, preferences=body['patch'],updated_at=utc())
        record = {'body':data,'result':new}
        new_session = dict(s,memory_version=new['memory_version'])
        self.store.save(memories={key:new},updates={ukey:record},sessions={s['session_id']:new_session})
        self.memories[key],self.updates[ukey] = new,record
        s.update(new_session)
        self.emit('memory.updated',{'session_id':s['session_id'],'update_id':body['update_id'],'memory_version':new['memory_version']},s['operator_id'])
        return new

    def active(self, s):
        if s['state']!='ACTIVE' or stamp(s['expires_at'])<=time.time():
            raise Rejected('SESSION_NOT_ACTIVE')

    async def send(self, role, identifier, kind, payload):
        connection = self.connections.get((role,identifier))
        if connection:
            message = {'v':1,'message_id':uid('msg'),'sent_at':utc(),'type':kind,'payload':payload}
            self.validate('Message',message)
            try:
                await asyncio.wait_for(connection['ws'].send_json(message),1)
            except (ConnectionError,RuntimeError,asyncio.TimeoutError):
                return False
            return True
        return False

    def precheck(self, aid, shid):
        if aid not in self.agents or shid not in self.shells:
            raise Rejected('NOT_FOUND',404)
        sh = self.shells[shid]
        if ('agent',aid) not in self.connections:
            raise Rejected('AGENT_OFFLINE',503)
        if ('gateway',shid) not in self.connections:
            raise Rejected('SHELL_OFFLINE',503)
        if sh['current_session_id'] or sh['state'] in ('ACTIVE','CONNECTING','RELEASING'):
            raise Rejected('SHELL_BUSY')
        if not sh['enabled'] or sh['state']!='IDLE' or sh['gate']=='readonly':
            raise Rejected('SHELL_DISABLED',403)
        caps = set(self.agents[aid]['public']['capabilities']) & set(sh['allowed_actions']) & set(sh['capabilities'])
        if not caps:
            raise Rejected('CAPABILITY_UNSUPPORTED',422)
        return sorted(caps)

    async def create_session(self, owner, aid, shid, authorization=None):
        caps = self.precheck(aid,shid)
        if authorization:
            caps=sorted(set(caps) & set(authorization['capabilities']))
            if not caps:raise Rejected('CAPABILITY_UNSUPPORTED',422)
        if any(s['agent_id']==aid and (s['state'] in ('CONNECTING','ACTIVE','RELEASING') or self.shells.get(s['shell_id'],{}).get('current_session_id')==s['session_id']) for s in self.sessions.values()):
            raise Rejected('AGENT_BUSY')
        sid,epoch = uid('session'), self.epochs.get(shid,0)+1
        s = {'session_id':sid,'agent_id':aid,'shell_id':shid,'operator_id':owner,'state':'CONNECTING','lease_epoch':epoch,'expires_at':utc(time.time()+60),'memory_version':0}
        memory = self.memory(s)
        s['memory_version'] = memory['memory_version']
        self.store.save(sessions={sid:s},epochs={shid:epoch},session_authorizations={sid:authorization} if authorization else {})
        if authorization:self.nameplates.session_auth[sid]=authorization
        self.sessions[sid],self.epochs[shid],self.ready[sid] = s,epoch,set()
        self.deadlines[sid] = time.monotonic()+5
        sh = self.shells[shid]
        sh.update(state='CONNECTING',current_session_id=sid)
        self.agents[aid]['public']['status']='BUSY'
        self.emit('session.granted',{'session':s},owner)
        self.emit('shell.state',{'shell':sh})
        await self.send('gateway',shid,'session.offer',{'session':s,'permitted_capabilities':caps})
        await self.send('agent',aid,'session.offer',{'session':s,'memory':memory,'permitted_capabilities':caps})
        return s

    async def release(self, s, reason):
        if s['state'] in ('RELEASED','FAILED','RELEASING'):
            return
        s['state']='RELEASING'
        self.store.save(sessions={s['session_id']:s})
        sh = self.shells[s['shell_id']]
        if sh['state'] not in ('ESTOP','FAULT','OFFLINE'):
            sh['state']='RELEASING'
        self.deadlines[s['session_id']]=time.monotonic()+5
        p = {'session_id':s['session_id'],'lease_epoch':s['lease_epoch'],'reason':reason}
        await self.send('agent',s['agent_id'],'session.revoke',p)
        await self.send('gateway',s['shell_id'],'session.revoke',p)
        self.emit('shell.state',{'shell':sh})

    def snapshot(self, owner):
        sessions = [s for s in self.sessions.values() if s['operator_id']==owner][-100:]
        ids = {s['session_id'] for s in sessions}
        return {'v':1,'cursor':self.stream+':'+str(self.seq),'generated_at':utc(),'mode':self.cfg['mode'],
            'agents':[a['public'] for a in self.agents.values()], 'shells':list(self.shells.values()),
            'sessions':sessions,'commands':[c for c in self.commands.values() if c['request']['session_id'] in ids][-100:]}

    async def http(self, request):
        path,method = request.path,request.method
        if method=='POST':
            body = await request.json()
        else:
            body = {}
        if path in ('/v1/gateway/config','/v1/gateway/results'):
            role,shell_id=self.identity(self.bearer(request))
            if role!='gateway':
                raise Rejected('FORBIDDEN',403)
            if path.endswith('/config'):
                return web.json_response({'shell':self.shells[shell_id],'mode':self.cfg['mode'],
                    'profile':self.experiences.profile(self.shells[shell_id],self.cfg)})
            self.validate('GatewayResult',body)
            cid=body['command_id']
            async with self.lock:
                record=self.commands.get(cid)
                if not record or record['request']['session_id']!=body['session_id']:
                    raise Rejected('NOT_FOUND',404)
                session=self.sessions[body['session_id']]
                if session['shell_id']!=shell_id:
                    raise Rejected('FORBIDDEN',403)
                prior=self.gateway_receipts.get(cid)
                if prior:
                    if prior['outcome']!=body:
                        raise Rejected('IDEMPOTENCY_CONFLICT')
                    return web.json_response(prior['ack'])
                old=record['outcome']
                late=old['status']=='UNKNOWN'
                if old['status'] in ('COMPLETED','FAILED') and old!=body:
                    raise Rejected('IDEMPOTENCY_CONFLICT')
                ack={'command_id':cid,'stored':True,'late':late}
                receipt={'shell_id':shell_id,'received_at':utc(),'outcome':body,'ack':ack}
                changed=old['status'] in ('ACCEPTED','EXECUTING')
                if changed:
                    record=dict(record,outcome=body)
                self.store.save(commands={cid:record},gateway_receipts={cid:receipt})
                self.commands[cid]=record
                self.gateway_receipts[cid]=receipt
                self.experiences.finish(record,'gateway')
                if changed:
                    self.deadlines.pop('cmd:'+cid,None)
                    kind='action.completed' if body['status']=='COMPLETED' else 'action.failed'
                    self.emit(kind,body,session['operator_id'])
                    await self.send('agent',session['agent_id'],kind,body)
                return web.json_response(ack)
        if path=='/v1/operator-session':
            self.rate('login:'+str(request.remote))
            self.validate('OperatorLogin',body)
            owner = self.cfg['operator_codes'].get(body['access_code'])
            if not owner:
                raise Rejected('UNAUTHORIZED',401,'Invalid operator access code.')
            token = secrets.token_urlsafe(32)
            expiry = time.time()+8*3600
            self.cookies[digest(token)]=(owner,expiry)
            response = web.json_response({'operator_id':owner,'expires_at':utc(expiry)})
            response.set_cookie('summon_session',token,secure=self.cfg.get('secure_cookie',True),httponly=True,samesite='Strict',max_age=28800)
            return response
        if path=='/v1/catalog':
            details=request.query.getall('details',[])
            if details and (len(details)!=1 or details[0] not in ('0','1')):
                raise Rejected('INVALID_MESSAGE',400,'details must be 0 or 1, supplied at most once.')
            # Opt-in extension preserves strict 0.1.0 Catalog consumers.
            fields=('shell_id','label','state','capabilities')
            if request.query.get('details')=='1':
                fields+=('enabled','gate','identity_gates','stop_kind','allowed_actions')
            return web.json_response({'v':1,'agents':[a['public'] for a in self.agents.values()], 'shells':[{k:s[k] for k in fields} for s in self.shells.values()]})
        if path=='/v1/agents/me':
            role,aid=self.identity(self.bearer(request))
            if role!='agent':
                raise Rejected('FORBIDDEN',403)
            connection=self.connections.get((role,aid))
            return web.json_response({'agent':self.agents[aid]['public'],
                'connected':bool(connection and connection.get('welcomed_at')),
                'last_handshake_at':connection.get('welcomed_at') if connection else None})
        if path=='/v1/experiences':
            owner=self.operator(request)
            return web.json_response(self.experiences.query(owner,
                request.query.get('shell_id',''), request.query.get('capability',''), request.query.get('mode','')))
        if method=='GET' and path.startswith('/v1/sessions/') and path.endswith('/experiences'):
            s=self.access(request,request.match_info['sid'],True)
            self.active(s)
            context=self.experiences.context(s,self.shells[s['shell_id']],self.cfg)
            result=self.experiences.query(s['operator_id'],s['shell_id'],
                request.query.get('capability',''),self.cfg['mode'],context['profile_id'])
            if self.bearer(request):
                self.experiences.retrieved(s,result['total'])
            return web.json_response(result)
        if path=='/v1/agents':
            if not self.bearer(request):
                raise Rejected('UNAUTHORIZED',401,'Missing Authorization: Bearer <invite> header.')
            if not secrets.compare_digest(self.bearer(request),self.cfg['invite']):
                raise Rejected('UNAUTHORIZED',401,'Invalid registration invite; obtain the current invite from the deployment owner.')
            principal='invite'
        elif method=='GET' and path.startswith('/v1/sessions/') and path.endswith('/memory'):
            return web.json_response(self.memory(self.access(request,request.match_info['sid'],True)))
        elif method=='GET' and path.startswith('/v1/commands/'):
            self.reader(request,True)
            record=self.commands.get(request.match_info['cid'])
            if not record:
                raise Rejected('NOT_FOUND',404)
            self.access(request,record['request']['session_id'],True,False)
            return web.json_response(record)
        else:
            principal=self.operator(request)
        if path=='/v1/state':
            return web.json_response(self.snapshot(principal))
        if path=='/v1/events':
            return await self.sse(request,principal)
        names={'/v1/agents':'RegisterAgent','/v1/sessions':'CreateSession','inputs':'SubmitInput','feedback':'Feedback','release':'ReleaseSession','handoff':'HandoffSession'}
        self.validate(names.get(path,names.get(path.rsplit('/',1)[-1],'')),body)
        key=principal+':'+path+':'+body['request_id']
        async with self.lock:
            previous=self.idem.get(key)
            if previous:
                if previous['body']!=body:
                    raise Rejected('IDEMPOTENCY_CONFLICT')
                return web.json_response(previous['result'],status=previous['status'])
            status=202
            if path=='/v1/agents':
                self.rate('register',5)
                name=unicodedata.normalize('NFC',body['name'])
                if any(a['public']['name']==name for a in self.agents.values()):
                    raise Rejected('NAME_TAKEN')
                if len(self.agents)>=100:
                    raise Rejected('RATE_LIMITED',429)
                aid,token=uid('agent'),secrets.token_urlsafe(32)
                public={'agent_id':aid,'name':name,'bio':body['bio'],'capabilities':body['capabilities'],'status':'OFFLINE','summons':0,'last_landed_at':None}
                new_agent={'public':public,'token_hash':digest(token)}
                result={'agent':public,'agent_token':token,'address':'summon://'+aid+'?v=1'}
                status=201
                # Nameplate allocation and registration are one SQLite transaction.
                self.nameplates.ensure(aid)
                self.store.save(agents={aid:new_agent})
                self.agents[aid]=new_agent
            elif path=='/v1/sessions':
                s=await self.create_session(principal,body['agent_id'],body['shell_id'])
                result={'session':s}
            else:
                s=self.access(request,request.match_info['sid'])
                action=path.rsplit('/',1)[-1]
                if action=='feedback':
                    result=self.update_memory(s,body)
                    status=200
                elif action=='inputs':
                    result=await self.submit(s,body['text'])
                elif action=='release':
                    await self.release(s,'operator release')
                    result={'session':s}
                elif action=='handoff':
                    self.active(s)
                    self.precheck(s['agent_id'],body['target_shell_id'])
                    result={'handoff_id':uid('handoff'),'source_session_id':s['session_id'],'target_shell_id':body['target_shell_id'],'status':'PENDING'}
                    self.moves[s['session_id']]=dict(result)
                    await self.release(s,'handoff')
            entry=json.loads(canonical({'body':body,'result':result,'status':status}))
            self.store.save(idem={key:entry})
            self.idem[key]=entry
            return web.json_response(entry['result'],status=status)

    async def submit(self,s,text):
        self.nameplates.check_session(s)
        self.active(s)
        if s['session_id'] in self.inputs:
            raise Rejected('TASK_BUSY')
        iid=uid('input')
        self.inputs[s['session_id']]=iid
        await self.send('agent',s['agent_id'],'input.text',{'session_id':s['session_id'],'input_id':iid,'text':text,'memory_version':self.memory(s)['memory_version']})
        return {'session_id':s['session_id'],'input_id':iid,'status':'ACCEPTED'}

    async def sse(self,request,owner):
        cursor=request.query.get('after','')
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'})
        await response.prepare(request)
        try:
            n=int(cursor.rsplit(':',1)[1]) if cursor.startswith(self.stream+':') else -1
        except (ValueError,IndexError):
            n=-1
        try:
            while True:
                oldest=int(self.events[0][2]['event_id'].rsplit(':',1)[1]) if self.events else self.seq+1
                if n<oldest-1 or n>self.seq:
                    event={'v':1,'event_id':self.stream+':'+str(self.seq),'at':utc(),'mode':self.cfg['mode'],'type':'stream.reset','payload':{'reason':'CURSOR_EXPIRED'}}
                    await response.write(('data: '+canonical(event)+'\n\n').encode())
                    break
                for _,who,event in list(self.events):
                    number=int(event['event_id'].rsplit(':',1)[1])
                    if number>n:
                        if who is None or who==owner:
                            await response.write(('id: '+event['event_id']+'\ndata: '+canonical(event)+'\n\n').encode())
                        n=number
                await response.write(b': heartbeat\n\n')
                await asyncio.sleep(1)
                self.operator(request)
        except (ConnectionError,asyncio.CancelledError,Rejected):
            pass
        return response

    async def ws(self,request):
        identity=self.identity(self.bearer(request))
        if identity in self.connections:
            raise Rejected('CONNECTION_EXISTS')
        ws=web.WebSocketResponse(max_msg_size=16384)
        await ws.prepare(request)
        c={'ws':ws,'last':time.monotonic(),'id':uid('conn')}
        self.connections[identity]=c
        role,identifier=identity
        try:
            raw=await ws.receive(timeout=5)
            message=json.loads(raw.data)
            self.validate('Message',message)
            if message['type']!='hello' or message['payload']!={'role':role,'id':identifier}:
                raise Rejected('FORBIDDEN',403)
            c['last']=time.monotonic()
            await self.send(role,identifier,'welcome',{'connection_id':c['id'],'heartbeat_interval_ms':1000})
            c['welcomed_at']=utc()
            if role=='agent':
                self.agents[identifier]['public']['status']='ONLINE'
                self.emit('agent.state',{'agent':self.agents[identifier]['public']})
            async for raw in ws:
                if raw.type!=WSMsgType.TEXT:
                    break
                message=None
                try:
                    message=json.loads(raw.data)
                    self.validate('Message',message)
                    async with self.lock:
                        await self.message(identity,c,message)
                except (Rejected,ValueError,TypeError) as exc:
                    code=exc.code if isinstance(exc,Rejected) else 'INVALID_MESSAGE'
                    reply=message.get('message_id','unknown') if isinstance(message,dict) else 'unknown'
                    await self.send(role,identifier,'error',{'reply_to':reply,'error':error(code,reply)})
        except (Rejected,ValueError,TypeError,asyncio.TimeoutError):
            await ws.close(code=1008)
        finally:
            if self.connections.get(identity) is c:
                del self.connections[identity]
                async with self.lock:
                    if role=='agent':
                        self.agents[identifier]['public']['status']='OFFLINE'
                        self.emit('agent.state',{'agent':self.agents[identifier]['public']})
                    else:
                        self.shells[identifier].update(state='OFFLINE',enabled=False)
                        self.emit('shell.state',{'shell':self.shells[identifier]})
                    for s in list(self.sessions.values()):
                        if s['agent_id']==identifier or s['shell_id']==identifier:
                            await self.release(s,'connection lost')
        return ws

    async def message(self,identity,c,m):
        role,identifier=identity
        kind,p=m['type'],m['payload']
        if kind=='heartbeat':
            if p['connection_id']!=c['id']:
                raise Rejected('FORBIDDEN',403)
            c['last']=time.monotonic()
            return
        if kind=='shell.report':
            if role!='gateway' or p['shell_id']!=identifier:
                raise Rejected('FORBIDDEN',403)
            sh=self.shells[identifier]
            sh['last_seen_at']=utc()
            sh['enabled']=p['enabled'] and p['physical_state']=='READY'
            if p['physical_state']!='READY' or not p['enabled']:
                if p['physical_state']!='READY':
                    sh['state']=p['physical_state']
                if sh['current_session_id']:
                    await self.release(self.sessions[sh['current_session_id']],'physical fault')
            elif not sh['current_session_id']:
                sh['state']='IDLE'
            self.emit('shell.state',{'shell':sh})
            return
        sid=p.get('session_id')
        s=self.sessions.get(sid)
        if not s or (role=='agent' and s['agent_id']!=identifier) or (role=='gateway' and s['shell_id']!=identifier):
            raise Rejected('FORBIDDEN',403)
        if 'lease_epoch' in p and p['lease_epoch']!=s['lease_epoch']:
            raise Rejected('STALE_LEASE')
        if kind=='session.ready':
            self.nameplates.check_session(s)
            if s['state']!='CONNECTING' or p['role']!=role:
                raise Rejected('SESSION_NOT_ACTIVE')
            self.ready[sid].add(role)
            if self.ready[sid]=={'agent','gateway'}:
                s['state']='ACTIVE'
                self.store.save(sessions={sid:s})
                self.shells[s['shell_id']]['state']='ACTIVE'
                self.deadlines.pop(sid,None)
                for who,key in [('gateway',s['shell_id']),('agent',s['agent_id'])]:
                    await self.send(who,key,'session.activate',{'session_id':sid,'lease_epoch':s['lease_epoch']})
                if self.cfg['mode']=='LIVE':
                    a=self.agents[s['agent_id']]['public']
                    a['summons']+=1
                    a['last_landed_at']=utc()
                    self.store.save(agents={s['agent_id']:self.agents[s['agent_id']]})
                self.emit('session.active',{'session':s},s['operator_id'])
                move=self.moves.pop('target:'+sid,None)
                if move:
                    self.emit('handoff.completed',{'handoff_id':move['handoff_id'],'source_session_id':move['source_session_id'],'session':s},s['operator_id'])
            return
        if kind=='session.stopped' and role=='gateway':
            if s['state'] not in ('RELEASING','FAILED'):
                raise Rejected('SESSION_NOT_ACTIVE')
            if any(v['request']['session_id']==sid and v['outcome']['status'] in ('ACCEPTED','EXECUTING') for v in self.commands.values()):
                raise Rejected('COMMAND_BUSY')
            s['state']='RELEASED'
            self.store.save(sessions={sid:s})
            self.inputs.pop(sid,None)
            self.deadlines.pop(sid,None)
            sh=self.shells[s['shell_id']]
            sh['current_session_id']=None
            if sh['state'] not in ('FAULT','ESTOP','OFFLINE'):
                sh['state']='IDLE'
            self.agents[s['agent_id']]['public']['status']='ONLINE' if ('agent',s['agent_id']) in self.connections else 'OFFLINE'
            self.emit('session.released',{'session':s},s['operator_id'])
            move=self.moves.pop(sid,None)
            if move:
                try:
                    new=await self.create_session(s['operator_id'],s['agent_id'],move['target_shell_id'])
                    self.moves['target:'+new['session_id']]=move
                except Rejected as exc:
                    failed={k:move[k] for k in ('handoff_id','source_session_id','target_shell_id')}
                    failed['error']=error(exc.code,sid)
                    self.emit('handoff.failed',failed,s['operator_id'])
            return
        if kind=='action.request' and role=='agent':
            self.nameplates.check_session(s)
            cid=p['command_id']
            existing=self.commands.get(cid)
            if existing:
                if existing['request']!=p:
                    raise Rejected('IDEMPOTENCY_CONFLICT')
                outcome=existing['outcome']
                suffix={'ACCEPTED':'accepted','EXECUTING':'started','COMPLETED':'completed','FAILED':'failed','UNKNOWN':'failed'}[outcome['status']]
                await self.send('agent',identifier,'action.'+suffix,outcome)
                return
            self.active(s)
            if self.inputs.get(sid)!=p['input_id']:
                raise Rejected('FORBIDDEN',403)
            expiry=stamp(p['expires_at'])
            if expiry<=time.time() or expiry>stamp(s['expires_at']) or expiry>stamp(m['sent_at'])+5.1:
                raise Rejected('EXPIRED')
            if abs(stamp(m['sent_at'])-time.time())>.5:
                raise Rejected('CLOCK_SKEW',422)
            records=[v for v in self.commands.values() if v['request']['session_id']==sid]
            if any(v['outcome']['status'] in ('ACCEPTED','EXECUTING','UNKNOWN') for v in records):
                raise Rejected('COMMAND_BUSY')
            if p['seq']!=len(records)+1:
                raise Rejected('OUT_OF_ORDER')
            sh=self.shells[s['shell_id']]
            authorization=self.nameplates.session_auth.get(sid)
            if authorization and p['action']['capability'] not in authorization['capabilities']:
                raise Rejected('ACTION_NOT_ALLOWED',422)
            if not sh['enabled'] or sh['state']!='ACTIVE':
                raise Rejected('SHELL_DISABLED',403)
            if p['action']['capability'] not in sh['allowed_actions'] or p['action']['capability'] not in self.agents[identifier]['public']['capabilities']:
                raise Rejected('ACTION_NOT_ALLOWED',422)
            record={'request':p,'outcome':{'session_id':sid,'command_id':cid,'status':'ACCEPTED'}}
            context=self.experiences.context(s,sh,self.cfg)
            self.store.save(commands={cid:record},experience_contexts={cid:context})
            self.experiences.contexts[cid]=context
            self.commands[cid]=record
            self.deadlines['cmd:'+cid]=time.monotonic()+10
            await self.send('gateway',s['shell_id'],'action.request',p)
            return
        if kind in ('action.accepted','action.started','action.completed','action.failed') and role=='gateway':
            record=self.commands.get(p['command_id'])
            if not record or record['request']['session_id']!=sid:
                raise Rejected('NOT_FOUND',404)
            old=record['outcome']['status']
            if old in ('COMPLETED','FAILED','UNKNOWN'):
                if record['outcome']==p:
                    return
                raise Rejected('IDEMPOTENCY_CONFLICT')
            if kind=='action.accepted' and old=='EXECUTING':
                raise Rejected('OUT_OF_ORDER')
            record['outcome']=p
            self.store.save(commands={p['command_id']:record})
            self.experiences.finish(record,'gateway')
            if p['status'] in ('COMPLETED','FAILED','UNKNOWN'):
                self.deadlines.pop('cmd:'+p['command_id'],None)
            self.emit(kind,p,s['operator_id'])
            await self.send('agent',s['agent_id'],kind,p)
            return
        if kind=='input.finished' and role=='agent':
            self.active(s)
            if self.inputs.get(sid)!=p['input_id']:
                raise Rejected('NOT_FOUND',404)
            if any(v['request']['session_id']==sid and v['outcome']['status'] in ('ACCEPTED','EXECUTING') for v in self.commands.values()):
                raise Rejected('COMMAND_BUSY')
            self.inputs.pop(sid,None)
            self.emit(kind,p,s['operator_id'])
            return
        if kind=='memory.update' and role=='agent':
            try:
                memory=self.update_memory(s,p)
                await self.send(role,identifier,'memory.updated',{'session_id':sid,'update_id':p['update_id'],'memory':memory})
            except Rejected as exc:
                await self.send(role,identifier,'memory.failed',{'session_id':sid,'update_id':p['update_id'],'current_version':self.memory(s)['memory_version'],'error':error(exc.code,p['update_id'])})
            return
        if kind=='input.submit' and role=='gateway':
            key='gateway:'+identifier+':'+sid+':'+p['request_id']
            prev=self.idem.get(key)
            if prev:
                if prev['body']!=p:
                    raise Rejected('IDEMPOTENCY_CONFLICT')
                return
            result=await self.submit(s,p['text'])
            self.idem[key]={'body':p,'result':result,'status':202}
            self.store.save(idem={key:self.idem[key]})
            return
        raise Rejected('FORBIDDEN',403)

    async def tick(self):
        while True:
            await asyncio.sleep(.5)
            for (role,identifier),c in list(self.connections.items()):
                if not c.get('welcomed_at'):
                    continue  # ws() owns the bounded hello timeout; welcome must be first.
                if time.monotonic()-c['last']>3:
                    await c['ws'].close(code=1001)
                else:
                    await self.send(role,identifier,'heartbeat',{'connection_id':c['id']})
            async with self.lock:
                await self.nameplates.expire()
                for sid,s in list(self.sessions.items()):
                    if s['state']=='ACTIVE' and stamp(s['expires_at'])<=time.time():
                        await self.release(s,'lease expired')
                    if sid in self.deadlines and self.deadlines[sid]<time.monotonic():
                        self.deadlines.pop(sid,None)
                        if s['state']=='CONNECTING':
                            await self.release(s,'connect timeout')
                        elif s['state']=='RELEASING':
                            s['state']='FAILED'
                            self.store.save(sessions={sid:s})
                            self.shells[s['shell_id']]['state']='FAULT'
                            self.emit('session.failed',{'session':s,'error':error('HANDOFF_BLOCKED',sid)},s['operator_id'])
                            move=self.moves.pop(sid,None)
                            if move:
                                payload={k:move[k] for k in ('handoff_id','source_session_id','target_shell_id')}
                                payload['error']=error('HANDOFF_BLOCKED',sid)
                                self.emit('handoff.failed',payload,s['operator_id'])
                for key,deadline in list(self.deadlines.items()):
                    if key.startswith('cmd:') and deadline<time.monotonic():
                        self.deadlines.pop(key,None)
                        r=self.commands[key[4:]]
                        p=r['request']
                        r['outcome']={'session_id':p['session_id'],'command_id':p['command_id'],'status':'UNKNOWN','error':error('UNKNOWN',p['command_id'])}
                        self.store.save(commands={p['command_id']:r})
                        self.experiences.finish(r,'timeout')
                        s=self.sessions[p['session_id']]
                        self.emit('action.failed',r['outcome'],s['operator_id'])
                        await self.send('agent',s['agent_id'],'action.failed',r['outcome'])
                        await self.release(s,'unverified action timeout')


def create_app(path, cfg):
    hub=Hub(path,cfg)

    @web.middleware
    async def guard(request,handler):
        try:
            if request.match_info.http_exception is None and request.method=='POST' and request.path not in ('/v1/agents','/v1/gateway/results') and request.match_info.route not in request.app['machine_routes'] and request.headers.get('Origin')!=cfg['origin']:
                raise Rejected('FORBIDDEN',403,'Missing or mismatched Origin; write requests require the exact configured site origin.')
            response=await handler(request)
        except Rejected as exc:
            response=web.json_response(error(exc.code,message=exc.message),status=exc.status)
        except web.HTTPException as exc:
            code='NOT_FOUND' if exc.status==404 else 'INVALID_MESSAGE'
            response=web.json_response(error(code,message=exc.reason),status=exc.status)
            if exc.status==405:
                response.headers['Allow']=exc.headers.get('Allow','')
        except (json.JSONDecodeError,UnicodeDecodeError):
            response=web.json_response(error('INVALID_MESSAGE'),status=400)
        except sqlite3.Error:
            response=web.json_response(error('MEMORY_WRITE_FAILED'),status=500)
        response.headers['X-Content-Type-Options']='nosniff'
        if request.path.startswith('/assets/') and response.status==200:
            suffix=Path(request.path).suffix
            mime={'.md':'text/plain','.css':'text/css','.js':'text/javascript'}.get(suffix)
            if mime:
                response.headers['Content-Type']=mime+'; charset=utf-8'
                response.headers['Content-Disposition']='inline'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']=(
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "connect-src 'self'; frame-ancestors 'none'")
        return response

    app=web.Application(middlewares=[guard],client_max_size=16384)
    app['hub']=hub
    app['machine_routes']=set()
    hub.nameplates.routes(app)
    hub.onboarding.routes(app)
    hub.agent_dashboard.routes(app)
    for route in ['/v1/operator-session','/v1/agents','/v1/gateway/results','/v1/sessions','/v1/sessions/{sid}/inputs','/v1/sessions/{sid}/feedback','/v1/sessions/{sid}/release','/v1/sessions/{sid}/handoff']:
        app.router.add_post(route,hub.http)
    for route in ['/v1/catalog','/v1/agents/me','/v1/gateway/config','/v1/state','/v1/events','/v1/experiences','/v1/sessions/{sid}/experiences','/v1/sessions/{sid}/memory','/v1/commands/{cid}']:
        app.router.add_get(route,hub.http)
    app.router.add_get('/v1/connect',hub.ws)
    async def health(request):
        return web.json_response({'status':'ok','mode':cfg['mode'],'contract':'0.1.0'})
    async def index(request):
        return web.FileResponse(ROOT/'web/index.html')
    async def skill(request):
        return web.Response(text=(ROOT/'web/skill.md').read_text(encoding='utf-8'),
                            content_type='text/plain',charset='utf-8',
                            headers={'Content-Disposition':'inline'})
    app.router.add_get('/healthz',health)
    app.router.add_get('/skill.md',skill)
    app.router.add_get('/',index)
    if (ROOT/'web').exists():
        async def asset(request):
            root=(ROOT/'web').resolve()
            try:
                path=(root/request.match_info['filename']).resolve()
                path.relative_to(root)
                if not path.is_file():
                    raise web.HTTPNotFound()
            except (ValueError,OSError):
                raise web.HTTPNotFound()
            # Check before FileResponse.prepare(), where missing-file errors
            # would otherwise occur after the error middleware has returned.
            return web.FileResponse(path)
        app.router.add_get('/assets/{filename:.*}',asset)
    async def startup(app):
        hub.spawn(hub.tick())
    async def shutdown(app):
        for c in list(hub.connections.values()):
            await c['ws'].close(code=1001)
        for t in list(hub.tasks):
            t.cancel()
        await asyncio.gather(*hub.tasks,return_exceptions=True)
    async def cleanup(app):
        hub.store.db.close()
    app.on_startup.append(startup)
    app.on_shutdown.append(shutdown)
    app.on_cleanup.append(cleanup)
    return app


if __name__=='__main__':
    config=json.loads(Path(os.environ['SUMMON_CONFIG']).read_text())
    web.run_app(create_app(config['database'],config),host='127.0.0.1',port=config.get('port',8840),access_log=None)
