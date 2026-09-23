"""Short-lived, browser-to-Agent onboarding correlation.

An intent is only a rendezvous token. It cannot register an Agent, grant device
access, or mint a nameplate. The Agent must authenticate with its own token.
"""

import secrets
import time

from aiohttp import web


class Onboarding:
    def __init__(self, hub):
        self.hub = hub
        self.intents = {}

    def _intent(self, token):
        intent = self.intents.get(token)
        if not intent or intent['expires'] <= time.time():
            from hub.app import Rejected
            raise Rejected('NOT_FOUND', 404, '接入会话不存在或已过期，请重新开始。')
        return intent

    async def create(self, request):
        from hub.app import utc
        self.hub.rate('onboarding-create:' + str(request.remote), 10)
        body = await request.json()
        if body != {}:
            from hub.app import Rejected
            raise Rejected('INVALID_MESSAGE', 400)
        now = time.time()
        self.intents = {key: item for key, item in self.intents.items()
                        if item['expires'] > now}
        token = secrets.token_urlsafe(32)
        expiry = now + 1800
        self.intents[token] = {'expires': expiry, 'agent_id': None}
        return web.json_response({'token': token, 'expires_at': utc(expiry)}, status=201)

    async def claim(self, request):
        from hub.app import Rejected
        role, agent_id = self.hub.identity(self.hub.bearer(request))
        if role != 'agent':
            raise Rejected('FORBIDDEN', 403)
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {'token'} or not isinstance(body['token'], str):
            raise Rejected('INVALID_MESSAGE', 400)
        self.hub.rate('onboarding-claim:' + agent_id, 20)
        intent = self._intent(body['token'])
        if intent['agent_id'] and intent['agent_id'] != agent_id:
            raise Rejected('FORBIDDEN', 403, '此接入会话已被另一个 Agent 认领。')
        intent['agent_id'] = agent_id
        return web.json_response({'claimed': True, 'nameplate': self.hub.nameplates.ensure(agent_id)})

    async def status(self, request):
        from hub.app import Rejected
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {'token'} or not isinstance(body['token'], str):
            raise Rejected('INVALID_MESSAGE', 400)
        self.hub.rate('onboarding-status:' + str(request.remote), 120)
        intent = self._intent(body['token'])
        agent_id = intent['agent_id']
        if agent_id is None:
            return web.json_response({'state': 'WAITING'})
        agent = self.hub.agents.get(agent_id)
        if agent is None:
            raise Rejected('NOT_FOUND', 404)
        online = agent['public']['status'] in ('ONLINE', 'BUSY') and bool(
            self.hub.connections.get(('agent', agent_id), {}).get('welcomed_at'))
        return web.json_response({
            'state': 'ONLINE' if online else 'CLAIMED',
            'agent': {'agent_id': agent_id, 'name': agent['public']['name']},
            'nameplate': self.hub.nameplates.ensure(agent_id),
        })

    async def public_nameplate(self, request):
        self.hub.rate('nameplate-lookup:' + str(request.remote), 60)
        plate = self.hub.nameplates.public(request.match_info['code'])
        return web.json_response(plate)

    def routes(self, app):
        app.router.add_post('/v1/onboarding/intents', self.create)
        route = app.router.add_post('/v1/onboarding/claim', self.claim)
        app['machine_routes'].add(route)
        app.router.add_post('/v1/onboarding/status', self.status)
        app.router.add_get('/v1/nameplates/public/{code}', self.public_nameplate)
