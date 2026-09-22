import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills/summon-device-onboarding'


def module(name):
    spec=importlib.util.spec_from_file_location(name,SKILL/'scripts'/f'{name}.py')
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SkillTests(unittest.TestCase):
    def test_unknown_and_simulated_evidence_cannot_pass(self):
        assess=module('check_admission').assess
        data=json.loads((SKILL/'assets/admission-report.json').read_text(encoding='utf-8'))
        self.assertTrue(assess(data))
        for section in ('criteria','acceptance'):
            for item in data[section].values():
                item.update(status='pass',evidence=['test-log.json: independently review metrics'])
        data['device']={k:'observed-v1' for k in data['device']}
        data['capabilities']=['display.text']
        data['evidence_level']='simulated'
        self.assertTrue(assess(data))
        data['evidence_level']='physical'
        self.assertFalse(assess(data))
        motion=copy.deepcopy(data)
        motion['motion_device']=True
        self.assertTrue(assess(motion))
        motion['motion_stop']={'status':'pass','evidence':['stop-log.json']}
        self.assertFalse(assess(motion))
        data['criteria']['H6']['evidence']=[]
        self.assertTrue(assess(data))

    def test_install_preserves_existing_skill(self):
        install=module('install_skill').install
        with tempfile.TemporaryDirectory() as folder:
            target=install(SKILL,Path(folder)/'skills')
            original=(target/'SKILL.md').read_bytes()
            self.assertEqual(original,(SKILL/'SKILL.md').read_bytes())
            with self.assertRaises(FileExistsError):
                install(SKILL,Path(folder)/'skills')
            self.assertEqual((target/'SKILL.md').read_bytes(),original)

    def test_probe_does_not_claim_admission(self):
        from unittest.mock import patch
        probe=module('probe_hardware')
        with patch.object(probe,'run',return_value={'status':'ok','output':'Example USB'}):
            report=probe.probe()
        self.assertEqual(report['admission'],'NOT_ASSESSED')
        self.assertNotIn('credentials',report)
