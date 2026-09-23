"""One-time Agent-issued browser sign-in for a personal SUMMON dashboard."""

import re
import secrets
import time

from aiohttp import web

from hub.nameplates import ALPHABET, normalize


class AgentDashboard:
    def __init__(self, hub):
        self.hub = hub
        self.codes = {}
        self.sessions = {}

    def profile(self, agent_id):
        agent = self.hub.agents[agent_id]['public']
        return {
            'agent': {key: agent[key] for key in ('agent_id', 'name', 'status')},
            'nameplate': self.hub.nameplates.ensure(agent_id),
        }

    async def issue(self, request):
        from hub.app import Rejected, digest, utc
        role, agent_id = self.hub.identity(self.hub.bearer(request))
        if role != 'agent':
            raise Rejected('FORBIDDEN', 403)
        if await request.json() != {}:
            raise Rejected('INVALID_MESSAGE', 400)
        connection = self.hub.connections.get(('agent', agent_id))
        if not connection or not connection.get('welcomed_at'):
            raise Rejected('AGENT_OFFLINE', 409, 'Agent 尚未完成在线握手。')
        self.hub.rate('dashboard-code:' + agent_id, 5)
        now = time.time()
        self.codes = {key: value for key, value in self.codes.items() if value['expires'] > now}
        raw = ''.join(secrets.choice(ALPHABET) for _ in range(10))
        code = raw[:5] + '-' + raw[5:]
        expiry = now + 300
        self.codes[digest(raw)] = {'agent_id': agent_id, 'expires': expiry}
        return web.json_response({'code': code, 'expires_at': utc(expiry)}, status=201)

    async def redeem(self, request):
        from hub.app import Rejected, digest
        self.hub.rate('dashboard-redeem:' + str(request.remote), 8)
        body = await request.json()
        if not isinstance(body, dict) or set(body) not in ({'code'}, {'code', 'nameplate'}):
            raise Rejected('INVALID_MESSAGE', 400)
        if not isinstance(body['code'], str):
            raise Rejected('INVALID_MESSAGE', 400)
        raw = body['code'].strip().upper().replace('-', '')
        if not re.fullmatch(r'[0-9A-HJKMNP-TV-Z]{10}', raw):
            raise Rejected('UNAUTHORIZED', 401, '连接码无效或已过期。')
        key = digest(raw)
        entry = self.codes.get(key)
        if not entry or entry['expires'] <= time.time():
            raise Rejected('UNAUTHORIZED', 401, '连接码无效或已过期。')
        agent_id = entry['agent_id']
        if 'nameplate' in body:
            if not isinstance(body['nameplate'], str):
                raise Rejected('INVALID_MESSAGE', 400)
            try:
                expected = normalize(body['nameplate'])
            except ValueError:
                raise Rejected('INVALID_MESSAGE', 400)
            if not secrets.compare_digest(expected, self.hub.nameplates.ensure(agent_id)):
                raise Rejected('FORBIDDEN', 403, '连接码不属于这个铭牌。')
        connection = self.hub.connections.get(('agent', agent_id))
        if not connection or not connection.get('welcomed_at'):
            raise Rejected('AGENT_OFFLINE', 409, 'Agent 当前不在线，请先连接。')
        del self.codes[key]
        cookie = secrets.token_urlsafe(32)
        self.sessions[digest(cookie)] = {'agent_id': agent_id, 'expires': time.time() + 8 * 3600}
        response = web.json_response(self.profile(agent_id))
        response.set_cookie('summon_agent_session', cookie,
                            secure=self.hub.cfg.get('secure_cookie', True), httponly=True,
                            samesite='Strict', max_age=8 * 3600)
        return response

    def current_agent(self, request):
        from hub.app import Rejected, digest
        cookie = request.cookies.get('summon_agent_session', '')
        entry = self.sessions.get(digest(cookie)) if cookie else None
        if not entry or entry['expires'] <= time.time():
            raise Rejected('UNAUTHORIZED', 401, '请让 Agent 生成新的连接码。')
        return entry['agent_id']

    async def me(self, request):
        return web.json_response(self.profile(self.current_agent(request)))

    def routes(self, app):
        route = app.router.add_post('/v1/agent-login/codes', self.issue)
        app['machine_routes'].add(route)
        app.router.add_post('/v1/agent-login/redeem', self.redeem)
        app.router.add_get('/v1/agent-dashboard/me', self.me)
