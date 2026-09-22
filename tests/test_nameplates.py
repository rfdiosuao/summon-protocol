import asyncio
import copy
import time
import unittest

from tests import test_gateway as fixture
from gateway.nameplates import NameplateClient
from gateway.tui import InputState
from hub.nameplates import normalize


class NameplateTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.GatewayIntegrationTests.asyncSetUp
    asyncTearDown=fixture.GatewayIntegrationTests.asyncTearDown
    wait=fixture.GatewayIntegrationTests.wait

    async def pairing(self):
        headers={'Authorization':'Bearer gateway-secret'}
        response=await self.client.post('/v1/gateway/pairings',headers=headers,json={'request_id':'pair'})
        self.assertEqual(response.status,201)
        pair=await response.json()
        response=await self.client.post('/v1/device-pairings/preview',headers=self.headers,json={'request_id':'preview','user_code':pair['user_code']})
        self.assertEqual((await response.json())['shell_id'],'pc')
        approval={'request_id':'approve','user_code':pair['user_code'],'shell_id':'pc','capabilities':['display.text']}
        self.assertEqual((await self.client.post('/v1/device-pairings/approve',json=approval)).status,403)
        response=await self.client.post('/v1/device-pairings/approve',headers=self.headers,json=approval)
        self.assertEqual(response.status,200)
        path='/v1/gateway/pairings/'+pair['pairing_id']+'/claim'
        self.assertEqual((await self.client.post(path,headers={'Authorization':'Bearer other-secret'},json={'request_id':'claim'})).status,403)
        response=await self.client.post(path,headers=headers,json={'request_id':'claim'})
        grant=await response.json()
        self.assertEqual(grant,(await (await self.client.post(path,headers=headers,json={'request_id':'claim'})).json()))
        return grant

    async def test_pair_connect_execute_upload_release_and_revoke(self):
        hub=self.app['hub'];ui=NameplateClient(self.gateway)
        await ui.refresh()
        plate=ui.directory[0]
        self.assertEqual(normalize(plate['code'].lower().replace('-','')),plate['code'])
        await ui.lookup(plate['code'])
        with self.assertRaisesRegex(ValueError,'授权'):await ui.connect()
        ui.grant=await self.pairing()
        headers={'Authorization':'Bearer other-secret','X-Summon-Device-Grant':ui.grant['device_grant']}
        body={'request_id':'bad','code':plate['code'],'agent_id':plate['agent']['agent_id']}
        self.assertEqual((await self.client.post('/v1/gateway/sessions',headers=headers,json=body)).status,403)
        await ui.connect()
        sid=ui.session['session_id']
        await self.wait(lambda:hub.sessions[sid]['state']=='ACTIVE')
        await ui.status();await ui.submit('nameplate-roundtrip')
        await self.wait(lambda:bool(hub.gateway_receipts) and not self.gateway.journal.pending())
        self.assertIn('nameplate-roundtrip',self.output.getvalue())
        await ui.release()
        self.assertEqual(hub.sessions[sid]['state'],'RELEASED')
        # Reuse same approval for a new lease, then revoke from browser.
        await ui.connect();sid=ui.session['session_id']
        await self.wait(lambda:hub.sessions[sid]['state']=='ACTIVE')
        response=await self.client.post('/v1/device-authorizations/'+ui.grant['grant_id']+'/revoke',headers=self.headers,json={'request_id':'revoke'})
        self.assertEqual(response.status,200)
        await self.wait(lambda:hub.sessions[sid]['state']=='RELEASED')
        await ui.status()
        with self.assertRaisesRegex(ValueError,'授权'):await ui.connect()

    async def test_expiry_idempotency_and_database_uniqueness(self):
        hub=self.app['hub'];np=hub.nameplates
        grant=await self.pairing()
        plate=np.public(np.ensure(next(iter(hub.agents))))
        headers={'Authorization':'Bearer gateway-secret','X-Summon-Device-Grant':grant['device_grant']}
        body={'request_id':'connect-once','code':plate['code'],'agent_id':plate['agent']['agent_id']}
        a=await self.client.post('/v1/gateway/sessions',headers=headers,json=body)
        b=await self.client.post('/v1/gateway/sessions',headers=headers,json=body)
        first=await a.json();self.assertEqual(first,await b.json())
        sid=first['session']['session_id']
        await self.wait(lambda:hub.sessions[sid]['state']=='ACTIVE')
        changed=dict(body,code='SMN-0000-0000')
        self.assertEqual((await self.client.post('/v1/gateway/sessions',headers=headers,json=changed)).status,409)
        np.grants[grant['grant_id']]['expires']=time.time()-1
        await self.wait(lambda:hub.sessions[sid]['state']=='RELEASED')
        self.assertEqual((await self.client.post('/v1/gateway/sessions',headers=headers,json=body)).status,403)
        self.assertEqual(np.ensure(plate['agent']['agent_id']),plate['code'])
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            np.db.execute('INSERT INTO nameplates VALUES (?,?,?)',(plate['code'],'different','ACTIVE'))
        np.db.rollback()

    async def test_unapproved_pair_and_wrong_identity_rejected(self):
        headers={'Authorization':'Bearer gateway-secret'}
        pair=await (await self.client.post('/v1/gateway/pairings',headers=headers,json={'request_id':'pending'})).json()
        path='/v1/gateway/pairings/'+pair['pairing_id']+'/claim'
        self.assertEqual((await self.client.post(path,headers=headers,json={'request_id':'claim'})).status,403)
        self.app['hub'].nameplates.pairings[pair['pairing_id']]['expires']=time.time()-1
        self.assertEqual((await self.client.get('/v1/gateway/pairings/'+pair['pairing_id'],headers=headers)).status,404)
        self.assertEqual((await self.client.get('/v1/nameplates')).status,200)
        self.assertEqual((await self.client.get('/v1/agents/me/nameplate',headers=headers)).status,403)

    async def test_other_operator_cannot_revoke_and_invalid_caps_cannot_approve(self):
        hub=self.app['hub'];grant=await self.pairing()
        hub.cfg['operator_codes']['second']='second-owner'
        await self.client.post('/v1/operator-session',headers=self.headers,json={'access_code':'second'})
        response=await self.client.post('/v1/device-authorizations/'+grant['grant_id']+'/revoke',headers=self.headers,json={'request_id':'wrong-owner'})
        self.assertEqual(response.status,403)
        headers={'Authorization':'Bearer other-secret'}
        pair=await (await self.client.post('/v1/gateway/pairings',headers=headers,json={'request_id':'other-pair'})).json()
        response=await self.client.post('/v1/device-pairings/approve',headers=self.headers,json={
            'request_id':'wrong-caps','user_code':pair['user_code'],'shell_id':'other','capabilities':['arm.gesture']})
        self.assertEqual(response.status,403)

    async def test_nameplate_survives_reopen_and_does_not_change_registration_shape(self):
        from hub.app import Hub
        from pathlib import Path
        hub=self.app['hub'];aid=next(iter(hub.agents));code=hub.nameplates.ensure(aid)
        again=Hub(Path(self.tmp.name)/'hub.db',hub.cfg)
        try:self.assertEqual(again.nameplates.ensure(aid),code)
        finally:again.store.db.close()
        self.assertNotIn('nameplate',hub.agents[aid]['public'])


class KeyboardTests(unittest.TestCase):
    def test_input_shortcuts_are_text_and_escape_cancels(self):
        state=InputState();state.mode='plate'
        for char in 'SMN-QBBB-1234':self.assertIsNone(state.feed(char))
        self.assertEqual(state.feed('\r'),('plate','SMN-QBBB-1234'))
        self.assertEqual(state.feed('q'),('q',None))
        state.mode='task';state.feed('Q');state.feed('\x1b')
        self.assertIsNone(state.mode);self.assertEqual(state.text,'')
        state.feed('\xe0');self.assertIsNone(state.feed('M'))
