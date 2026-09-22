"""Check documented admission gates, not the truth of supplied evidence."""
import argparse
import json
from pathlib import Path

ACCEPTANCE=('cold_start','soak','interactions','latency','handoff','faults','rejection','experience')


def structure(report):
    errors=[]
    if not isinstance(report,dict):
        return ['Report must be an object']
    if report.get('stage') not in ('candidate','adapting','integrated','demo_passed'):
        errors.append('stage: expected candidate/adapting/integrated/demo_passed')
    if report.get('evidence_level') not in ('documentation','build','simulated','single_device','physical'):
        errors.append('evidence_level: invalid value')
    for section,keys in [('criteria',tuple('H'+str(i) for i in range(1,9))),('acceptance',ACCEPTANCE)]:
        values=report.get(section,{})
        for key in keys:
            item=values.get(key) if isinstance(values,dict) else None
            if not isinstance(item,dict) or item.get('status') not in ('unknown','pass','fail') or not isinstance(item.get('evidence'),list) or not all(isinstance(e,str) and e.strip() for e in item.get('evidence',[])):
                errors.append(section+'.'+key+': invalid status/evidence structure')
    return errors


def assess(report):
    blockers=structure(report)
    def passed(value):
        return (isinstance(value,dict) and value.get('status')=='pass'
                and isinstance(value.get('evidence'),list) and len(value['evidence'])>0
                and all(isinstance(e,str) and e.strip() for e in value['evidence']))
    if not isinstance(report,dict):
        return ['Report must be an object']
    if report.get('stage') in ('integrated','demo_passed') and report.get('evidence_level') not in ('single_device','physical'):
        blockers.append('stage: integrated/demo_passed requires real device evidence')
    if report.get('stage')=='demo_passed' and report.get('evidence_level')!='physical':
        blockers.append('stage: demo_passed requires physical acceptance evidence')
    for section,keys in [('criteria',tuple('H'+str(i) for i in range(1,9))),('acceptance',ACCEPTANCE)]:
        values=report.get(section)
        for key in keys:
            if not isinstance(values,dict) or not passed(values.get(key)):
                blockers.append(section+'.'+key+': requires pass with evidence')
    if report.get('evidence_level')!='physical':
        blockers.append('physical evidence required; documentation/build/simulation cannot pass')
    device=report.get('device')
    for key in ('model','firmware','transport','sdk_revision'):
        value=device.get(key) if isinstance(device,dict) else None
        if not isinstance(value,str) or not value.strip() or value.lower()=='unknown':
            blockers.append('device.'+key+': specify observed value or justified not-applicable')
    caps=report.get('capabilities')
    if not isinstance(caps,list) or not caps or not all(isinstance(c,str) and c.strip() for c in caps):
        blockers.append('at least one implemented capability required')
    if type(report.get('motion_device')) is not bool:
        blockers.append('motion_device must be an explicit boolean')
    if report.get('motion_device') is True and not passed(report.get('motion_stop')):
        blockers.append('motion_stop: requires pass with evidence')
    if blockers and report.get('stage')=='demo_passed':
        blockers.append('stage: demo_passed contradicts unresolved blockers')
    return blockers


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    args=parser.parse_args()
    try:
        report=json.loads(args.report.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        print(json.dumps({'reason':'invalid_input','error':str(exc)}))
        return 2
    malformed=structure(report)
    if malformed:
        print(json.dumps({'reason':'invalid_report','errors':malformed},ensure_ascii=False))
        return 2
    blockers=assess(report)
    print(json.dumps({'reason':'incomplete' if blockers else 'complete','stage':report.get('stage'),'demo_admissible_on_report':not blockers,'blockers':blockers,
                      'note':'Checks report completeness only; evidence must be independently verified.'},ensure_ascii=False,indent=2))
    return 3 if blockers else 0


if __name__=='__main__':
    raise SystemExit(main())
