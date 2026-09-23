import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.evox_backend import complete


class EvoxTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_prompt_and_result_are_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);exe=root/'evox.exe';exe.touch()
            secret=root/'private.json';secret.write_text(json.dumps({'api_key':'private-test-key'}))
            config=dict(executable=str(exe),agent_dir=directory,provider='test',model='test',credential_file=str(secret))
            async def spawn(*argv,**kwargs):
                self.assertNotIn('private-test-key',' '.join(argv))
                self.assertEqual(kwargs['env']['SUMMON_MODEL_KEY'],'private-test-key')
                self.assertIn('--no-tools',argv)
                prompt=Path(next(a[1:] for a in argv if a.startswith('@')))
                self.assertEqual(json.loads(prompt.read_text())[0]['content'],'private-prompt')
                Path(argv[argv.index('--output-last-message')+1]).write_text('{"reply":"ok"}')
                class Process:
                    returncode=0
                    async def wait(self):return 0
                return Process()
            with patch('hub.evox_backend.asyncio.create_subprocess_exec',spawn):
                self.assertEqual(await complete(config,[{'role':'user','content':'private-prompt'}],1),'{"reply":"ok"}')
            self.assertEqual(list(root.glob('summon-turn-*')),[])

    async def test_timeout_kills_planner_and_cleans_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);exe=root/'evox.exe';exe.touch()
            class Process:
                returncode=None
                killed=False
                async def wait(self):
                    if not self.killed:await asyncio.sleep(10)
                    return self.returncode
                def kill(self):self.killed=True;self.returncode=-1
            process=Process()
            async def spawn(*args,**kwargs):return process
            with patch('hub.evox_backend.asyncio.create_subprocess_exec',spawn):
                with self.assertRaises(asyncio.TimeoutError):
                    await complete(dict(executable=str(exe),agent_dir=directory,provider='x',model='y'),[],.01)
            self.assertTrue(process.killed)
            self.assertEqual(list(root.glob('summon-turn-*')),[])
