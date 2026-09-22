import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator,FormatChecker
from gateway.adapters import DesktopAdapter


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_https_allowlisted_url_is_opened(self):
        url='https://summon.entermodetwo.com/#test'
        calls=[]
        adapter=DesktopAdapter({'allowed_urls':[url]},opener=lambda value,**kwargs:calls.append(value) or True)
        request={'action':{'capability':'browser.open','args':{'url':url}}}
        result=await adapter.execute(request)
        self.assertEqual(calls,[url]);self.assertEqual(result['evidence'],'device_ack')
        for bad in ('https://example.com/','https://summon.entermodetwo.com.evil.test/','file:///C:/Windows/notepad.exe','javascript:alert(1)','https://a@som.test/','https://summon.entermodetwo.com/#different'):
            request['action']['args']['url']=bad
            with self.assertRaises(ValueError):await adapter.execute(request)
        self.assertEqual(len(calls),1)
        adapter.opener=lambda *a,**k:False
        request['action']['args']['url']=url
        with self.assertRaises(RuntimeError):await adapter.execute(request)

    async def test_browser_action_schema_requires_https_and_rejects_script_fields(self):
        schema=json.loads((Path(__file__).resolve().parents[1]/'protocol/summon.schema.json').read_text(encoding='utf-8'))
        validator=Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/Action'},format_checker=FormatChecker())
        action={'capability':'browser.open','args':{'url':'https://summon.entermodetwo.com/'}}
        self.assertTrue(validator.is_valid(action))
        action['args']['script']='arbitrary'
        self.assertFalse(validator.is_valid(action))
