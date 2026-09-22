import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills/summon-device-onboarding'


def module(name):
    spec=importlib.util.spec_from_file_location(name,SKILL/'scripts'/f'{name}.py')
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SkillTests(unittest.TestCase):
    def test_stage_structure_and_cli_exit_codes(self):
        checker=module('check_admission')
        data=json.loads((SKILL/'assets/admission-report.json').read_text(encoding='utf-8'))
        self.assertFalse(checker.structure(data))
        data['stage']='demo_passed'
        self.assertTrue(any('stage' in x for x in checker.assess(data)))
        data['criteria']['H1']['evidence']='not-a-list'
        self.assertTrue(checker.structure(data))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'report.json'
            command=[sys.executable,str(SKILL/'scripts/check_admission.py'),str(path)]
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,2)
            path.write_text((SKILL/'assets/admission-report.json').read_text(encoding='utf-8'),encoding='utf-8')
            result=subprocess.run(command,capture_output=True)
            self.assertEqual(result.returncode,3)
            self.assertEqual(json.loads(result.stdout)['reason'],'incomplete')
            path.write_text('{',encoding='utf-8')
            self.assertEqual(subprocess.run(command,capture_output=True).returncode,2)

    def test_windows_structured_devices_and_output_reuse(self):
        from unittest.mock import patch
        probe=module('probe_hardware')
        value={'status':'ok','output':json.dumps([{'Name':'蓝牙设备 (COM7)','PNPDeviceID':'test-device','PNPClass':'Ports'}],ensure_ascii=False)}
        with patch.object(probe.platform,'system',return_value='Windows'),patch.object(probe,'run',return_value=value):
            data=probe.probe()
        self.assertEqual(data['discovery']['serial_candidates'],['COM7'])
        self.assertEqual(data['discovery']['devices'][0]['Name'],'蓝牙设备 (COM7)')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'probe.json'
            path.write_text('preserve',encoding='utf-8')
            with patch.object(sys,'argv',['probe','--output',str(path)]),patch.object(probe,'probe',return_value=data):
                with self.assertRaises(SystemExit) as exc:
                    probe.main()
            self.assertEqual(exc.exception.code,2)
            self.assertEqual(path.read_text(),'preserve')

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
