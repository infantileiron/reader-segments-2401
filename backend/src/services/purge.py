import json,os
from datetime import datetime,timezone
from pathlib import Path
from src.models.entities import Reader,Segment,Outreach,Purge
from src.services.analysis import recalc_after_removal
ART=Path(os.environ.get('ARTIFACTS_DIR','/artifacts'))
def now():return datetime.now(timezone.utc).isoformat()
def artifact_path(sid):return ART/(str(sid)+'.json')
def write_artifact(sid,result):
 ART.mkdir(parents=True,exist_ok=True)
 p=artifact_path(sid);tmp=p.with_name(p.name+'.tmp')
 tmp.write_text(json.dumps(result,ensure_ascii=False));os.replace(tmp,p)
def remove_artifact(sid):
 p=artifact_path(sid)
 for f in (p,p.with_name(p.name+'.tmp')):
  try:f.unlink()
  except FileNotFoundError:pass
def find_impact(s,code):
 segs=[]
 for r in s.query(Segment).all():
  res=r.result or {}
  if any(x.get('code')==code for x in res.get('snapshot') or []) or any(x.get('code')==code for x in res.get('members') or []):segs.append(r.id)
 outs=[o.id for o in s.query(Outreach).all() if code in (o.members or [])]
 return {'segments':segs,'outreaches':outs}
def active_locks(s):
 segs=set();outs=set()
 for p in s.query(Purge).filter(Purge.state.in_(['pending','running'])).all():
  impact=p.impact or {};segs.update(impact.get('segments') or []);outs.update(impact.get('outreaches') or [])
 return segs,outs
def active_purge_codes(s):return [p.reader_code for p in s.query(Purge).filter(Purge.state.in_(['pending','running'])).all()]
def strip_snapshot(row,code):
 res=dict(row.result or {});snap=res.get('snapshot') or []
 kept=[x for x in snap if x.get('code')!=code]
 if len(kept)!=len(snap):res['snapshot']=kept;row.result=res
def run_purge(s,purge):
 '''执行永久清除：分群快照/成员→触达冻结名单→阅读档案→对账。每步幂等，失败重试不重复扣减；queued/running分群用条件更新作废，并发计算结果写不回来。'''
 code=purge.reader_code;stored=purge.impact or {'segments':[],'outreaches':[]}
 fresh=find_impact(s,code) # 与创建时快照合并，兜住并发新建的版本/方案
 impact={'segments':sorted(set(stored.get('segments') or [])|set(fresh['segments'])),'outreaches':sorted(set(stored.get('outreaches') or [])|set(fresh['outreaches']))}
 purge.impact=impact
 total=len(impact['segments'])+len(impact['outreaches'])+2
 purge.state='running';purge.error='';purge.progress={'phase':'开始清除','done':0,'total':total};purge.updated=now();s.commit()
 done=0
 for sid in impact['segments']:
  row=s.get(Segment,sid)
  if row:
   if row.state in ('queued','running'):
    n=s.query(Segment).filter(Segment.id==sid,Segment.state.in_(['queued','running'])).update({'state':'failed','error':'会员数据已撤回，该版本作废，请修订后新建版本'})
    s.commit();s.expire_all();row=s.get(Segment,sid)
    if n:remove_artifact(sid);strip_snapshot(row,code)
    elif row.state=='done':row.result=recalc_after_removal(row.result or {},code)
   elif row.state=='done':row.result=recalc_after_removal(row.result or {},code)
   elif row.state=='failed':strip_snapshot(row,code)
   s.commit()
   if row.state=='done':write_artifact(sid,row.result)
  done+=1;purge.progress={'phase':'清理分群版本 #'+str(sid),'done':done,'total':total};purge.updated=now();s.commit()
 for oid in impact['outreaches']:
  row=s.get(Outreach,oid)
  if row and code in (row.members or []):
   row.members=[m for m in row.members if m!=code];s.commit()
  done+=1;purge.progress={'phase':'清理触达方案 #'+str(oid),'done':done,'total':total};purge.updated=now();s.commit()
 s.query(Reader).filter_by(code=code).delete();s.commit()
 done+=1;purge.progress={'phase':'删除阅读档案','done':done,'total':total};purge.updated=now();s.commit()
 report=reconcile(s,code,impact);purge.report=report;done+=1
 if report['consistent']:purge.state='done';purge.progress={'phase':'完成','done':total,'total':total}
 else:purge.state='failed';purge.error='清除后对账不一致，请重试；重试不会重复扣减'
 purge.updated=now();s.commit()
def reconcile(s,code,impact):
 '''对账：档案已删、各版本快照/成员/群名单不含该会员且人数自洽、产物文件与库内一致、触达名单不含该会员。'''
 checks=[];ok=True
 for sid in impact['segments']:
  row=s.get(Segment,sid)
  if not row:continue
  res=row.result or {};members=res.get('members') or [];snap=res.get('snapshot') or []
  c=all(m.get('code')!=code for m in members) and all(x.get('code')!=code for x in snap) and all(code not in (p.get('member_codes') or []) for p in res.get('profiles') or [])
  if row.state=='done':
   c=c and res.get('n')==len(members) and sum(p.get('size',0) for p in res.get('profiles') or [])==len(members)
   try:
    disk=json.loads(artifact_path(sid).read_text())
    c=c and all(m.get('code')!=code for m in disk.get('members') or []) and disk.get('n')==len(disk.get('members') or [])
   except Exception:c=False
  ok=ok and c
  checks.append({'segment_id':sid,'state':row.state,'n':res.get('n'),'members':len(members),'analyzable':res.get('analyzable',True),'consistent':bool(c)})
 for oid in impact['outreaches']:
  row=s.get(Outreach,oid)
  if not row:continue
  c=code not in (row.members or []);ok=ok and c
  checks.append({'outreach_id':oid,'remaining':len(row.members or []),'consistent':bool(c)})
 deleted=not s.query(Reader).filter_by(code=code).first()
 ok=ok and deleted
 return {'reader_code':code,'reader_deleted':bool(deleted),'checks':checks,'consistent':bool(ok),'finished_at':now()}
