#!/usr/bin/env python3
import argparse,json,os,datetime,subprocess,sys,uuid,hashlib
def project_root():
 try:
  d=os.path.abspath(subprocess.check_output(['git','rev-parse','--git-common-dir'],text=True,stderr=subprocess.DEVNULL).strip())
  return os.path.dirname(d) if os.path.basename(d)=='.git' else d
 except Exception: return os.getcwd()
PR=project_root()
ROOT=os.path.join(os.path.expanduser('~/.claude/verified-diamond/state'),os.path.basename(PR)+'-'+hashlib.sha256(PR.encode()).hexdigest()[:8])
CUR=os.path.join(ROOT,'current-task.json')
FUP=os.path.join(ROOT,'followups.json')
def load():
 os.makedirs(ROOT,exist_ok=True)
 return json.load(open(CUR)) if os.path.exists(CUR) else {'status':'idle','task_graph':[],'artifacts':[],'approvals':[]}
def save(x):
 x['updated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with open(CUR,'w') as f: json.dump(x,f,indent=2)
def load_fup():
 os.makedirs(ROOT,exist_ok=True)
 return json.load(open(FUP)) if os.path.exists(FUP) else {'schema_version':1,'privacy':'metadata-only; never store prompts/source/secrets/transcripts','items':[]}
def save_fup(x):
 open_=[q for q in x['items'] if q.get('status') in ('pending','blocked')]
 done=[q for q in x['items'] if q.get('status') not in ('pending','blocked')][-20:]
 x['items']=open_+done
 x['updated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with open(FUP,'w') as f: json.dump(x,f,indent=2)
def main():
 p=argparse.ArgumentParser(); s=p.add_subparsers(dest='cmd',required=True)
 a=s.add_parser('init'); a.add_argument('--title',required=True)
 a=s.add_parser('add-task'); a.add_argument('--json',required=True)
 a=s.add_parser('result'); a.add_argument('--id',required=True); a.add_argument('--status',choices=['pass','fail','blocked'],required=True); a.add_argument('--evidence',default='')
 s.add_parser('ready'); s.add_parser('status'); a=s.add_parser('approve'); a.add_argument('--gate',required=True); a.add_argument('--by',required=True)
 a=s.add_parser('followup-add'); a.add_argument('--json',required=True)
 s.add_parser('followup-ready')
 a=s.add_parser('followup-result'); a.add_argument('--id',required=True); a.add_argument('--status',choices=['pass','fail','blocked','ejected'],required=True); a.add_argument('--evidence',default=''); a.add_argument('--escalated',action='store_true')
 z=p.parse_args(); x=load()
 if z.cmd=='init':
  try: rev=subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
  except Exception: rev=None
  x={'status':'planning','task_id':str(uuid.uuid4()),'title':z.title,'base_revision':rev,'task_graph':[],'artifacts':[],'approvals':[]}; save(x)
 elif z.cmd=='add-task':
  t=json.loads(z.json); required=['id','role','title','dependencies','owned_paths','forbidden_paths','acceptance','verify']
  miss=[k for k in required if k not in t]
  if miss: raise SystemExit('Missing task fields: '+','.join(miss))
  if any(set(t['owned_paths']) & set(q.get('owned_paths',[])) for q in x['task_graph']): raise SystemExit('Overlapping owned paths are forbidden')
  t['status']='pending'; x['task_graph'].append(t); save(x)
 elif z.cmd=='result':
  t=next((q for q in x['task_graph'] if q['id']==z.id),None)
  if not t: raise SystemExit('Unknown task')
  t['status']=z.status; t['evidence']=z.evidence; save(x)
 elif z.cmd=='ready':
  done={q['id'] for q in x['task_graph'] if q.get('status')=='pass'}
  print(json.dumps([q for q in x['task_graph'] if q.get('status')=='pending' and set(q['dependencies'])<=done],indent=2))
 elif z.cmd=='approve': x['approvals'].append({'gate':z.gate,'by':z.by,'time':datetime.datetime.now(datetime.timezone.utc).isoformat()}); save(x)
 elif z.cmd=='followup-add':
  f=load_fup(); t=json.loads(z.json); required=['id','objective','worker_class','owned_paths','forbidden_paths','acceptance','verify','risk']
  miss=[k for k in required if k not in t]
  if miss: raise SystemExit('Missing follow-up fields: '+','.join(miss))
  if t['risk'] not in ('R0','R1'): raise SystemExit('R2+ follow-ups must run the full verified diamond, not the fast lane')
  if not t['verify'] or not t['owned_paths']: raise SystemExit('fast-lane packet requires non-empty owned_paths and verify')
  if any(q['id']==t['id'] for q in f['items']): raise SystemExit('Duplicate follow-up id')
  t.setdefault('lane','fast'); t.setdefault('status','pending'); t.setdefault('retry_limit',1); f['items'].append(t); save_fup(f)
 elif z.cmd=='followup-ready':
  f=load_fup(); print(json.dumps([q for q in f['items'] if q.get('status')=='pending'],indent=2))
 elif z.cmd=='followup-result':
  f=load_fup(); t=next((q for q in f['items'] if q['id']==z.id),None)
  if not t: raise SystemExit('Unknown follow-up')
  t['status']=z.status; t['evidence']=z.evidence; t['escalated']=bool(z.escalated); save_fup(f)
 else: print(json.dumps(x,indent=2))
if __name__=='__main__': main()
