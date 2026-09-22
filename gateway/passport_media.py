"""Bounded USB audio frames; no cloud credential is sent to the Passport."""
import base64
import json


class Recording:
    def __init__(self):
        self.turn=None
        self.pcm=bytearray()
        self.seq=0
        self.nameplate=''

    def feed(self,frame):
        kind=frame.get('type')
        if kind=='record.start':
            self.turn=frame['turn'];self.pcm.clear();self.seq=0
            self.nameplate=frame.get('nameplate','')
        elif frame.get('turn')==self.turn and self.turn is not None:
            if kind=='record.chunk':
                try:
                    pcm=base64.b64decode(frame['pcm'],validate=True)
                    if frame['seq']!=self.seq or not pcm or len(pcm)>1024 or len(pcm)%2 or len(self.pcm)+len(pcm)>256000:
                        raise ValueError('Invalid audio sequence or length')
                except Exception:
                    self.turn=None;self.pcm.clear();raise ValueError('USB audio rejected') from None
                self.pcm.extend(pcm);self.seq+=1
            elif kind=='record.end':
                pcm=bytes(self.pcm);self.pcm.clear();self.turn=None
                if len(pcm)!=frame['bytes'] or len(pcm)<3200:raise ValueError('Incomplete USB recording')
                return pcm
            elif kind in ('record.cancel','cancel'):
                self.turn=None;self.pcm.clear()


def decode_line(line):
    if len(line)>2048:return None
    at=line.find(b'SUMMON1 ')
    if at<0:return None
    try:
        value=json.loads(line[at+8:])
        return value if isinstance(value,dict) else None
    except (ValueError,UnicodeError):return None
