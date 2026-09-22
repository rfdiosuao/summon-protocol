"""Check documented admission gates, not the truth of supplied evidence."""
import argparse
import json
from pathlib import Path

ACCEPTANCE=('cold_start','soak','interactions','latency','handoff','faults','rejection','experience')


def assess(report):
    blockers=[]
    def passed(value):
        return (isinstance(value,dict) and value.get('status')=='pass'
                and isinstance(value.get('evidence'),list) and len(value['evidence'])>0
                and all(isinstance(e,str) and e.strip() for e in value['evidence']))
    if not isinstance(report,dict):
        return ['Report must be an object']
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
    return blockers


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    args=parser.parse_args()
    try:
        blockers=assess(json.loads(args.report.read_text(encoding='utf-8')))
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({'demo_admissible_on_report':not blockers,'blockers':blockers,
                      'note':'Checks report completeness only; evidence must be independently verified.'},ensure_ascii=False,indent=2))
    return 2 if blockers else 0


if __name__=='__main__':
    raise SystemExit(main())
