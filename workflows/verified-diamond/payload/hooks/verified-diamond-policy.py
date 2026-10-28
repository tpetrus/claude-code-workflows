#!/usr/bin/env python3
import sys, json, os, re, datetime, hashlib
raw=sys.stdin.read()
try: event=json.loads(raw or '{}')
except Exception: event={}
phase=sys.argv[1] if len(sys.argv)>1 else 'unknown'
text=json.dumps(event,ensure_ascii=False)
blocked=[r'(?i)git\s+push\s+--force',r'(?i)git\s+reset\s+--hard',r'(?i)terraform\s+(apply|destroy)',r'(?i)kubectl\s+delete',r'(?i)\b(drop\s+database|truncate\s+table)\b',r'(?i)curl\b.*\|\s*(ba)?sh',r'(?i)wget\b.*\|\s*(ba)?sh']
secret_paths=[r'(?i)(^|[/\\])\.env($|[./\\])',r'(?i)(^|[/\\])\.ssh([/\\]|$)',r'(?i)(credentials?|private[_-]?key|secret)[^/\\]*$']
if phase=='pre':
    if any(re.search(p,text) for p in blocked+secret_paths):
        print(json.dumps({'decision':'block','reason':'Verified Diamond policy blocked a destructive, secret-bearing, or exfiltration-prone operation.'}))
        sys.exit(2)
# Metadata-only local audit; no tool input/output is persisted. Scoped per project; separate file from orchestration telemetry.
cwd=event.get('cwd') or os.getcwd()
slug=os.path.basename(cwd)+'-'+hashlib.sha256(cwd.encode()).hexdigest()[:8]
state=os.path.expanduser(os.path.join('~/.claude/verified-diamond/state',slug,'hook-telemetry.json'))
try:
    os.makedirs(os.path.dirname(state),exist_ok=True)
    data=json.load(open(state)) if os.path.exists(state) else {'schema_version':1,'runs':[]}
    data['runs'].append({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'phase':phase,'tool':event.get('tool_name'),'success':event.get('success')})
    data['runs']=data['runs'][-1000:]
    with open(state,'w') as f: json.dump(data,f,indent=2)
except Exception: pass
sys.exit(0)
