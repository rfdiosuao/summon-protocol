"""Durable command deduplication and cloud result outbox; never replay execution."""
import hashlib
import json
import sqlite3


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def failed(request,code='UNKNOWN'):
    return {'session_id':request['session_id'],'command_id':request['command_id'],'status':'UNKNOWN' if code=='UNKNOWN' else 'FAILED',
            'error':{'error':{'code':code,'message':'Execution unverified or stopped; inspect local device.','retryable':False,'request_id':request['command_id']}}}


class Journal:
    def __init__(self,path):
        self.guard=sqlite3.connect(str(path)+'.lock',timeout=0)
        try:
            self.guard.execute('BEGIN EXCLUSIVE')
        except sqlite3.OperationalError:
            self.guard.close()
            raise ValueError('Gateway journal is already in use by another process')
        self.db=sqlite3.connect(str(path))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, hash TEXT, session TEXT, seq INTEGER, outcome TEXT, uploaded INTEGER NOT NULL DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT)')
        # A power loss after reservation never authorizes another execution.
        for cid,sid in self.db.execute('SELECT id,session FROM commands WHERE outcome IS NULL').fetchall():
            self.finish(failed({'command_id':cid,'session_id':sid}))

    def reserve(self,request):
        previous=self.get(request['command_id'])
        if previous:
            if previous['hash']!=fingerprint(request):
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return previous
        with self.db:
            self.db.execute('INSERT INTO commands(id,hash,session,seq) VALUES(?,?,?,?)',
                            (request['command_id'],fingerprint(request),request['session_id'],request['seq']))
        return None

    def get(self,cid):
        row=self.db.execute('SELECT hash,outcome,uploaded FROM commands WHERE id=?',(cid,)).fetchone()
        return {'hash':row[0],'outcome':json.loads(row[1]) if row[1] else None,'uploaded':bool(row[2])} if row else None

    def finish(self,outcome):
        with self.db:
            self.db.execute('UPDATE commands SET outcome=?,uploaded=0 WHERE id=? AND outcome IS NULL',(json.dumps(outcome),outcome['command_id']))

    def pending(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT outcome FROM commands WHERE outcome IS NOT NULL AND uploaded=0')]

    def acknowledge(self,cid):
        with self.db:
            self.db.execute('UPDATE commands SET uploaded=1 WHERE id=?',(cid,))

    def epoch(self,value=None):
        row=self.db.execute("SELECT value FROM metadata WHERE key='epoch'").fetchone()
        current=int(row[0]) if row else 0
        if value is not None:
            if value<=current:
                raise ValueError('STALE_LEASE')
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES('epoch',?)",(str(value),))
        return current

    def metadata(self,key,value=None):
        if value is not None:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',(key,json.dumps(value)))
        row=self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self):
        self.db.close()
        self.guard.close()
