import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Segment,Purge,Audit
from src.services.analysis import cluster
from src.services.purge import run_purge,write_artifact,remove_artifact,now,active_purge_codes
r=redis.Redis.from_url(os.environ['REDIS_URL'])
def recover():
 '''启动恢复：queued/running的分群与pending/running的清除任务重新入队；清除执行幂等，重跑不重复扣减。'''
 with Session() as s:
  for p in s.query(Segment).filter(Segment.state.in_(['queued','running'])).all():
   p.state='queued';r.rpush('reader:segments',p.id)
  for p in s.query(Purge).filter(Purge.state.in_(['pending','running'])).all():
   p.state='pending';r.rpush('reader:purges',p.id)
  s.commit()
def sysaudit(s,action):s.add(Audit(actor='system',action=action,created=now()))
def process_segment(sid):
 with Session() as s:
  row=s.get(Segment,sid)
  if not row or row.state!='queued':return
  snapshot=(row.result or {}).get('snapshot') or []
  purging=active_purge_codes(s) # 兜底：清除任务活跃期间不得计算含被撤会员的快照
  if any(x.get('code') in purging for x in snapshot):
   row.state='failed';row.error='会员数据已撤回，该版本作废，请修订后新建版本'
   row.result={'snapshot':[x for x in snapshot if x.get('code') not in purging]}
   s.commit();remove_artifact(row.id);return
  row.state='running';s.commit()
  try:
   result={**cluster(snapshot,row.features,row.k),'snapshot':snapshot}
   write_artifact(row.id,result)
   n=s.query(Segment).filter(Segment.id==row.id,Segment.state=='running').update({'state':'done','result':result,'error':''})
   s.commit()
   if not n:remove_artifact(row.id)
  except Exception as e:
   s.rollback()
   s.query(Segment).filter(Segment.id==row.id,Segment.state=='running').update({'state':'failed','error':str(e)})
   s.commit();remove_artifact(row.id)
def process_purge(pid):
 with Session() as s:
  row=s.get(Purge,pid)
  if not row or row.state!='pending':return
  try:
   run_purge(s,row)
   sysaudit(s,('完成' if row.state=='done' else '失败')+'会员数据清除 '+row.reader_code);s.commit()
  except Exception as e:
   s.rollback();row=s.get(Purge,pid)
   if row:row.state='failed';row.error='清除执行异常：'+str(e);row.updated=now();sysaudit(s,'清除任务异常 '+row.reader_code);s.commit()
def main():
 recover()
 while True:
  Path('/tmp/heartbeat').touch()
  try:
   item=r.blpop(['reader:segments','reader:purges'],timeout=3)
   if not item:continue
   key=item[0].decode() if isinstance(item[0],bytes) else item[0]
   if key=='reader:segments':process_segment(int(item[1]))
   else:process_purge(int(item[1]))
  except Exception:logging.exception('worker');time.sleep(2)
if __name__=='__main__':main()
