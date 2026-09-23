"""Nameplate directory and operator-approved, shell-scoped device grants."""
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path

from aiohttp import web
from jsonschema import Draft202012Validator


ALPHABET='0123456789ABCDEFGHJKMNPQRSTVWXYZ'


def normalize(value):
    code=value.strip().upper().replace('-','')
    if not re.fullmatch(r'SMN[0-9A-HJKMNP-TV-Z]{8}',code):
        raise ValueError('铭牌格式应为 SMN-XXXX-XXXX')
    return 'SMN-'+code[3:7]+'-'+code[7:]


class Nameplates:
    def __init__(self,hub):
        from hub.app import Rejected,digest,utc,uid
        self.hub,self.Rejected,self.digest,self.utc,self.uid=hub,Rejected,digest,utc,uid
        self.pairings,self.grants,self.requests={},{},{}
        self.session_auth=hub.store.all('session_authorizations')
        # Display metadata only; grants still expire on Hub restart.
        self.device_links=hub.store.all('device_links')
        # Pairing secrets and device grants intentionally expire on Hub restart.
        self.db=hub.store.db
        self.db.execute('CREATE TABLE IF NOT EXISTS nameplates (code TEXT PRIMARY KEY, agent_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL)')
        with self.db:
            for aid in hub.agents:self.ensure(aid)
        schema=json.loads((Path(__file__).resolve().parents[1]/'protocol/nameplate.schema.json').read_text(encoding='utf-8'))
        self.validators={k:Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/'+k}) for k in schema['$defs']}

    def ensure(self,aid):
        row=self.db.execute('SELECT code FROM nameplates WHERE agent_id=?',(aid,)).fetchone()
        if row:return row[0]
        for _ in range(32):
            raw=''.join(secrets.choice(ALPHABET) for _ in range(8))
            code='SMN-'+raw[:4]+'-'+raw[4:]
            try:
                self.db.execute('INSERT INTO nameplates VALUES (?,?,?)',(code,aid,'ACTIVE'))
                return code
            except sqlite3.IntegrityError:continue
        raise RuntimeError('Could not allocate a unique nameplate')

    def validate(self,name,body):
        if not self.validators[name].is_valid(body):
            raise self.Rejected('INVALID_MESSAGE',400)

    def gateway(self,request):
        role,sid=self.hub.identity(self.hub.bearer(request))
        if role!='gateway':raise self.Rejected('FORBIDDEN',403)
        return sid

    def device_owner(self,request):
        if request.cookies.get('summon_agent_session'):
            return 'agent:'+self.hub.agent_dashboard.current_agent(request)
        return self.hub.operator(request)

    def public(self,code):
        try:code=normalize(code)
        except ValueError as exc:raise self.Rejected('INVALID_MESSAGE',400,str(exc))
        row=self.db.execute('SELECT agent_id,status FROM nameplates WHERE code=?',(code,)).fetchone()
        if not row or row[1]!='ACTIVE' or row[0] not in self.hub.agents:
            raise self.Rejected('NOT_FOUND',404,'铭牌不存在或已撤销。')
        a=self.hub.agents[row[0]]['public']
        return {'code':code,'agent':{k:a[k] for k in ('agent_id','name','bio','capabilities','status')}}

    def valid_grant(self,g):
        return g and not g['revoked'] and g['expires']>time.time()

    def dashboard_devices(self,agent_id):
        """Return this Agent's device map without pairing secrets."""
        pc=next((sh for sh in self.hub.shells.values() if 'command.exec' in sh['capabilities']
                 or 'browser.open' in sh['capabilities']),None)
        def item(shell_id,label,default=False):
            shell=self.hub.shells.get(shell_id) if shell_id else None
            grants=[g for g in self.grants.values() if g['agent_id']==agent_id
                    and g['shell_id']==shell_id and self.valid_grant(g)]
            supported=set(self.hub.agents[agent_id]['public']['capabilities'])
            if shell:supported &= set(shell['capabilities']) & set(shell['allowed_actions'])
            else:supported.clear()
            session=next((s for s in self.hub.sessions.values() if s['agent_id']==agent_id
                          and s['shell_id']==shell_id and s['state'] in ('CONNECTING','ACTIVE','RELEASING')),None)
            return {'shell_id':shell_id or 'default-computer','label':label,
                    'kind':'computer' if default else 'hardware',
                    'state':shell['state'] if shell else 'OFFLINE',
                    'authorized':bool(grants),
                    'capabilities':sorted({cap for g in grants for cap in g['capabilities']} & supported),
                    'session_state':session['state'] if session else None}
        devices=[item(pc['shell_id'] if pc else None,'电脑客户端',True)]
        links=sorted((link for link in self.device_links.values() if link['agent_id']==agent_id),
                     key=lambda link:(link['label'],link['shell_id']))
        for link in links:
            if pc and link['shell_id']==pc['shell_id']:
                continue
            devices.append(item(link['shell_id'],link['label']))
        return devices

    def grant(self,request,shell):
        token=request.headers.get('X-Summon-Device-Grant','')
        g=next((g for g in self.grants.values() if secrets.compare_digest(g['hash'],self.digest(token))),None) if token else None
        if not self.valid_grant(g) or g['shell_id']!=shell:
            raise self.Rejected('FORBIDDEN',403,'设备授权缺失、过期或不属于本设备，请重新配对。')
        return g

    def check_session(self,s):
        auth=self.session_auth.get(s['session_id'])
        if auth and not self.valid_grant(self.grants.get(auth['grant_id'])):
            raise self.Rejected('FORBIDDEN',403,'设备授权已过期或撤销。')

    async def expire(self):
        now=time.time()
        for s in list(self.hub.sessions.values()):
            if s['state'] in ('CONNECTING','ACTIVE') and s['session_id'] in self.session_auth:
                if not self.valid_grant(self.grants.get(self.session_auth[s['session_id']]['grant_id'])):
                    await self.hub.release(s,'device authorization expired or revoked')
        for key,p in list(self.pairings.items()):
            if p['expires']<now:
                del self.pairings[key]  # Removes short-lived codes and claim recovery secrets.
        for key,r in list(self.requests.items()):
            if r['expires']<now:del self.requests[key]
        for key,g in list(self.grants.items()):
            if g['expires']<now:del self.grants[key]

    def pairing(self,pid,shell=None):
        p=self.pairings.get(pid)
        if not p or p['expires']<=time.time():raise self.Rejected('NOT_FOUND',404,'配对请求已过期，请重新发起。')
        if shell and p['shell_id']!=shell:raise self.Rejected('FORBIDDEN',403)
        return p

    def code_pairing(self,code):
        normalized=code.strip().upper().replace('-','')
        p=next((p for p in self.pairings.values() if secrets.compare_digest(p['code_hash'],self.digest(normalized)) and p['expires']>time.time()),None)
        if not p:raise self.Rejected('NOT_FOUND',404,'配对码无效或已过期。')
        return p

    def summary(self,s):
        if not s:return None
        return {k:s[k] for k in ('session_id','agent_id','shell_id','state','expires_at')}

    def response(self,kind,body,status=200):
        self.validate(kind,body)
        return web.json_response(body,status=status)

    async def http(self,request):
        h=self.hub
        path=request.path
        body=await request.json() if request.method=='POST' else {}
        if path=='/v1/agents/me/nameplate':
            role,aid=h.identity(h.bearer(request))
            if role!='agent':raise self.Rejected('FORBIDDEN',403)
            code=self.db.execute('SELECT code FROM nameplates WHERE agent_id=?',(aid,)).fetchone()[0]
            return self.response('Nameplate',self.public(code))
        if path.startswith('/v1/nameplates'):
            if h.bearer(request):principal='gateway:'+self.gateway(request)
            else:principal='operator:'+self.device_owner(request)
            h.rate('nameplates:'+principal,120)
            scoped=principal[len('operator:agent:'):] if principal.startswith('operator:agent:') else None
            if 'code' in request.match_info:
                plate=self.public(request.match_info['code'])
                if scoped and plate['agent']['agent_id']!=scoped:raise self.Rejected('FORBIDDEN',403)
                return self.response('Nameplate',plate)
            items=[self.public(r[0]) for r in self.db.execute("SELECT code FROM nameplates WHERE status='ACTIVE' ORDER BY code")]
            if scoped:items=[item for item in items if item['agent']['agent_id']==scoped]
            return self.response('NameplateDirectory',{'items':items})
        browser=path.startswith('/v1/device-')
        shell=None if browser else self.gateway(request)
        owner=self.device_owner(request) if browser else None
        if request.method=='GET':
            if path=='/v1/gateway/session':
                sid=h.shells[shell]['current_session_id']
                s=h.sessions.get(sid)
                return self.response('DeviceSession',{'session':self.summary(s)})
            h.rate('pair-poll:'+shell,120)
            p=self.pairing(request.match_info['pid'],shell)
            return self.response('PairingStatus',{'pairing_id':p['id'],'status':p['status'],'interval':2,'expires_at':self.utc(p['expires'])})
        kind=('PairingApproval' if path.endswith('/approve') else 'PairingPreview' if path.endswith('/preview') else
              'DeviceConnect' if path=='/v1/gateway/sessions' else 'DeviceInput' if path.endswith('/inputs') else 'NameplateRequest')
        self.validate(kind,body)
        # Machine idempotency is bound to both shell and grant where appropriate.
        g=None
        if path=='/v1/gateway/sessions' or path.endswith('/inputs'):g=self.grant(request,shell)
        principal=owner if browser else shell+(':'+g['id'] if g else '')
        key=principal+':'+path+':'+body['request_id']
        async with h.lock:
            old=self.requests.get(key)
            if old and old['expires']>time.time():
                if old['body']!=body:raise self.Rejected('IDEMPOTENCY_CONFLICT')
                return web.json_response(old['result'],status=old['status'])
            status=200
            ttl=time.time()+86400
            if path=='/v1/gateway/pairings':
                h.rate('pairing:'+shell,5)
                # One live pairing per shell, repeated requests cannot approve a hidden replacement.
                existing=next((p for p in self.pairings.values() if p['shell_id']==shell and p['expires']>time.time() and p['status']=='PENDING'),None)
                if existing:p=existing
                else:
                    code=''.join(secrets.choice(ALPHABET) for _ in range(10))
                    while any(p['code_hash']==self.digest(code) for p in self.pairings.values()):
                        code=''.join(secrets.choice(ALPHABET) for _ in range(10))
                    pid=self.uid('pair')
                    p={'id':pid,'shell_id':shell,'code_hash':self.digest(code),'code':code[:5]+'-'+code[5:],
                       'expires':time.time()+300,'status':'PENDING'}
                    self.pairings[pid]=p
                ttl=p['expires']
                result={'pairing_id':p['id'],'user_code':p['code'],'verification_uri':h.cfg['origin']+'/assets/device.html',
                        'expires_at':self.utc(p['expires']),'interval':2}
                result_kind='PairingCreated';status=201
            elif path in ('/v1/device-pairings/preview','/v1/device-pairings/approve'):
                h.rate('pair-code:'+owner,20)
                p=self.code_pairing(body['user_code']);sh=h.shells[p['shell_id']]
                ttl=p['expires']
                if p['status']!='PENDING':raise self.Rejected('IDEMPOTENCY_CONFLICT',409,'此配对请求已经处理。')
                if path.endswith('/preview'):
                    result={'shell_id':sh['shell_id'],'label':sh['label'],'capabilities':sh['allowed_actions'],
                            'mode':h.cfg['mode'],'expires_at':self.utc(p['expires'])}
                    result_kind='PairingDevice'
                else:
                    caps=body['capabilities']
                    if body['shell_id']!=sh['shell_id'] or not set(caps).issubset(sh['allowed_actions']):
                        raise self.Rejected('FORBIDDEN',403)
                    if sh['gate']=='readonly':raise self.Rejected('SHELL_DISABLED',403)
                    token=secrets.token_urlsafe(32);gid=self.uid('grant')
                    grant={'id':gid,'hash':self.digest(token),'shell_id':sh['shell_id'],'operator_id':owner,
                           'agent_id':owner[6:] if owner.startswith('agent:') else None,
                           'capabilities':caps,'expires':time.time()+28800,'revoked':False}
                    self.grants[gid]=grant
                    if grant['agent_id']:
                        link={'agent_id':grant['agent_id'],'shell_id':sh['shell_id'],'label':sh['label']}
                        link_key=grant['agent_id']+':'+sh['shell_id']
                        self.device_links[link_key]=link
                        self.hub.store.save(device_links={link_key:link})
                    p.update(status='APPROVED',grant_id=gid,claim_token=token)
                    result={'grant_id':gid,'expires_at':self.utc(grant['expires'])};result_kind='DeviceApproval'
            elif path.endswith('/claim'):
                p=self.pairing(request.match_info['pid'],shell)
                if p['status']!='APPROVED':raise self.Rejected('FORBIDDEN',403,'尚未获得用户批准。')
                grant=self.grants.get(p['grant_id'])
                if not self.valid_grant(grant):raise self.Rejected('FORBIDDEN',403)
                ttl=p['expires']
                result={'grant_id':grant['id'],'device_grant':p['claim_token'],'expires_at':self.utc(grant['expires']),
                        'capabilities':grant['capabilities']};result_kind='DeviceGrant'
            elif path=='/v1/gateway/sessions':
                plate=self.public(body['code'])
                if plate['agent']['agent_id']!=body['agent_id']:raise self.Rejected('IDEMPOTENCY_CONFLICT')
                if g.get('agent_id') and g['agent_id']!=body['agent_id']:raise self.Rejected('FORBIDDEN',403)
                caps=sorted(set(h.precheck(body['agent_id'],shell)) & set(g['capabilities']))
                if not caps:raise self.Rejected('CAPABILITY_UNSUPPORTED',422)
                s=await h.create_session(g['operator_id'],body['agent_id'],shell,
                                        authorization={'grant_id':g['id'],'capabilities':caps})
                result={'session':self.summary(s)};result_kind='DeviceSession';status=202
            elif path.endswith('/revoke'):
                grant=self.grants.get(request.match_info['gid'])
                if not grant:raise self.Rejected('NOT_FOUND',404)
                if grant['operator_id']!=owner:raise self.Rejected('FORBIDDEN',403)
                grant['revoked']=True
                await self.expire()
                result={'revoked':True};result_kind='DeviceRevoked'
            else:
                s=h.sessions.get(request.match_info['sid'])
                if not s:raise self.Rejected('NOT_FOUND',404)
                if s['shell_id']!=shell:raise self.Rejected('FORBIDDEN',403)
                if path.endswith('/release'):
                    await h.release(s,'local device release')
                    result={'session':self.summary(s)};result_kind='DeviceSession';status=202
                else:
                    auth=self.session_auth.get(s['session_id'])
                    if not auth or auth['grant_id']!=g['id'] or s['operator_id']!=g['operator_id']:
                        raise self.Rejected('FORBIDDEN',403)
                    result=await h.submit(s,body['text']);result_kind='InputAccepted';status=202
            if result_kind=='InputAccepted':h.validate(result_kind,result)
            else:self.validate(result_kind,result)
            self.requests[key]={'body':body,'result':result,'status':status,'expires':ttl}
            return web.json_response(result,status=status)

    def routes(self,app):
        machine=['/v1/gateway/pairings','/v1/gateway/pairings/{pid}/claim','/v1/gateway/sessions',
                 '/v1/gateway/sessions/{sid}/release','/v1/gateway/sessions/{sid}/inputs']
        for path in machine+['/v1/device-pairings/preview','/v1/device-pairings/approve','/v1/device-authorizations/{gid}/revoke']:
            route=app.router.add_post(path,self.http)
            if path in machine:app['machine_routes'].add(route)
        for path in ['/v1/agents/me/nameplate','/v1/nameplates','/v1/nameplates/{code}',
                     '/v1/gateway/pairings/{pid}','/v1/gateway/session']:
            app.router.add_get(path,self.http)
