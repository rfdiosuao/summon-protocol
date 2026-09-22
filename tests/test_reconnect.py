import asyncio
import unittest
from unittest.mock import patch

import aiohttp
from hub.simulator import DemoFleet


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_connections_back_off_and_stable_connection_resets(self):
        fleet=DemoFleet('http://unused','',{})
        waits=[]
        async def connect(role,identifier,token,timing):
            # First two fail before welcome; third remained healthy for 11 seconds.
            if len(waits)==2:
                timing['welcome']=89
        async def sleep(delay):
            waits.append(delay)
            if len(waits)==4:
                raise asyncio.CancelledError()
        fleet.connection=connect
        with patch('hub.simulator.time.monotonic',return_value=100),patch('hub.simulator.random.uniform',side_effect=lambda low,high:low),patch('hub.simulator.asyncio.sleep',side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):
                await fleet.run('agent','a','token')
        self.assertEqual(waits,[1,2,1,2])

    async def test_invalid_credentials_stop_reconnect(self):
        fleet=DemoFleet('http://unused','',{})
        async def connect(*args):
            raise aiohttp.WSServerHandshakeError(None,(),status=401)
        fleet.connection=connect
        with self.assertRaisesRegex(RuntimeError,'Credentials rejected'):
            await fleet.run('agent','a','token')
