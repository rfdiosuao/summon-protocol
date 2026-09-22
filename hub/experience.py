"""Private, evidence-based device history. Never stores utterances or action bodies."""
import hashlib
import json
import time
from datetime import datetime, timezone


class ExperienceLedger:
    def __init__(self, store):
        self.store = store
        self.contexts = store.all('experience_contexts')
        self.records = store.all('experiences')
        self.reads = store.all('experience_reads')

    @staticmethod
    def profile(shell, cfg):
        configured = cfg.get('device_profiles', {}).get(shell['shell_id'], {})
        profile = {key: str(configured.get(key, 'unspecified'))[:100]
                   for key in ('model', 'firmware', 'adapter_version')}
        profile['capabilities'] = sorted(shell['capabilities'])
        profile['allowed_actions'] = sorted(shell['allowed_actions'])
        return profile

    def context(self, session, shell, cfg):
        profile = self.profile(shell, cfg)
        fingerprint = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:20]
        return dict(operator_id=session['operator_id'], agent_id=session['agent_id'],
                    shell_id=shell['shell_id'], mode=cfg['mode'], profile=profile,
                    profile_id=fingerprint, submitted_at=time.time())

    def finish(self, record, source):
        request, outcome = record['request'], record['outcome']
        cid = request['command_id']
        context = self.contexts.get(cid)
        if not context or cid in self.records or outcome['status'] not in ('COMPLETED', 'FAILED', 'UNKNOWN'):
            return
        item = dict(context, experience_id=cid, command_id=cid, session_id=request['session_id'],
                    capability=request['action']['capability'], status=outcome['status'],
                    evidence=outcome.get('evidence', 'none'), source=source,
                    duration_ms=max(0, round((time.time()-context['submitted_at'])*1000)),
                    created_at=datetime.now(timezone.utc).isoformat(),
                    scope='operator', kind='execution_evidence')
        # No raw args, free-text errors, outputs, audio, or user inputs enter this ledger.
        self.store.save(experiences={cid:item})
        self.records[cid] = item

    def query(self, owner, shell_id='', capability='', mode='', profile_id=''):
        items = [dict(v) for v in self.records.values() if v['operator_id']==owner
                 and (not shell_id or v['shell_id']==shell_id)
                 and (not capability or v['capability']==capability)
                 and (not mode or v['mode']==mode)
                 and (not profile_id or v['profile_id']==profile_id)]
        items.sort(key=lambda v:v['created_at'], reverse=True)
        groups = {}
        for v in items:
            key = (v['shell_id'], v['capability'], v['mode'], v['profile_id'])
            g = groups.setdefault(key, dict(shell_id=v['shell_id'], capability=v['capability'],
                mode=v['mode'], profile_id=v['profile_id'], profile=v['profile'],
                completed=0, failed=0, unknown=0, total=0, durations=[]))
            g[v['status'].lower()] += 1
            g['total'] += 1
            g['durations'].append(v['duration_ms'])
        for g in groups.values():
            g['average_duration_ms'] = round(sum(g.pop('durations'))/g['total'])
            g['guidance'] = ('存在失败或未知结果：先核对设备状态，未知动作不要自动重放。'
                             if g['failed'] or g['unknown'] else
                             '已有完成回执：可参考相同设备版本与能力的历史，仍需校验当前授权。')
            g['validation'] = 'simulated' if g['mode']=='SIMULATED' else 'gateway_reported'
        return {'v':1, 'scope':'operator', 'total':len(items), 'items':items[:100],
                'groups':list(groups.values()), 'returned':min(len(items),100),
                'retrievals':sum(v['count'] for v in self.reads.values() if v['operator_id']==owner),
                'note':'执行回执不是任务成功证明；统计建议不是训练后的控制策略。'}

    def retrieved(self, session, count):
        key = session['session_id']
        previous = self.reads.get(key, {'operator_id':session['operator_id'], 'count':0})
        record = dict(previous, count=previous['count']+1, last_result_count=count)
        self.store.save(experience_reads={key:record})
        self.reads[key] = record
