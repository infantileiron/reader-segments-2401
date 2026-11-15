import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Reader,Segment,Outreach,Audit,Purge
from src.schemas.business import Login,ReaderInput,SegmentInput,OutreachInput,PurgeInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.purge import find_impact,active_locks,now
from src.controllers.business import ensure_segment
router=APIRouter()
def push(queue,i):
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush(queue,i);return True
 except redis.RedisError:return False
def mask_segment(d,locked):
 if d['id'] in locked:d['result']=None;d['purging']=True
 return d
def mask_outreach(d,locked):
 if d['id'] in locked:d['members']=[];d['purging']=True
 return d
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/readers')
def readers(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Reader).order_by(Reader.id).all()]
@router.post('/readers')
def create(data:ReaderInput,u=Depends(editor),s=Depends(db)):
 r=Reader(**data.model_dump());s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'会员编号重复')
 audit(s,u,'录入阅读档案 '+r.code);return dump(r)
@router.put('/readers/{i}')
def edit(i:int,data:ReaderInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Reader,i)
 if not r:raise HTTPException(404,'会员档案不存在')
 if s.query(Purge).filter(Purge.reader_code==r.code,Purge.state.in_(['pending','running'])).first():raise HTTPException(409,'该会员数据清除进行中，暂停修订')
 for k,v in data.model_dump().items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'会员编号重复')
 audit(s,u,'修订档案 '+str(i));return dump(r)
@router.get('/segments')
def segments(u=Depends(current),s=Depends(db)):
 locked,_=active_locks(s)
 return [mask_segment(dump(r),locked) for r in s.query(Segment).order_by(Segment.id.desc()).all()]
@router.post('/segments')
def train(data:SegmentInput,u=Depends(editor),s=Depends(db)):
 if s.query(Purge).filter(Purge.state.in_(['pending','running'])).count():raise HTTPException(409,'有进行中的会员数据清除任务，完成后再提交分群')
 rows=[dump(r) for r in s.query(Reader).order_by(Reader.id).all()];r=Segment(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 if not push('reader:segments',r.id):r.state='failed';r.error='队列暂不可用，请稍后重试';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交分群 '+str(r.id));return dump(r)
@router.get('/segments/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):
 locked,_=active_locks(s)
 return mask_segment(dump(ensure_segment(s,i)),locked)
@router.get('/segments/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_segment(s,i)
 locked,_=active_locks(s)
 if i in locked:raise HTTPException(409,'该版本涉及进行中的会员数据清除，暂停导出')
 if r.state!='done':raise HTTPException(409,'仅成功分群可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-segments-{i}.json"'})
@router.get('/outreaches')
def outreaches(u=Depends(current),s=Depends(db)):
 _,locked=active_locks(s)
 return [mask_outreach(dump(r),locked) for r in s.query(Outreach).order_by(Outreach.id.desc()).all()]
@router.post('/outreaches')
def outreach(data:OutreachInput,u=Depends(editor),s=Depends(db)):
 r=ensure_segment(s,data.segment_id)
 locked,_=active_locks(s)
 if data.segment_id in locked:raise HTTPException(409,'该版本涉及进行中的会员数据清除，暂停起草')
 if r.state!='done':raise HTTPException(409,'分群完成后才能制定触达方案')
 members=[m['code'] for m in r.result['members'] if m['cluster_id']==data.cluster_id]
 if not members:raise HTTPException(422,'该版本没有所选群组')
 o=Outreach(**data.model_dump(),members=members);s.add(o);s.commit();audit(s,u,'起草触达方案 '+str(o.id));return dump(o)
@router.post('/outreaches/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=s.get(Outreach,i)
 if not r:raise HTTPException(404,'触达方案不存在')
 _,locked=active_locks(s)
 if i in locked:raise HTTPException(409,'该方案涉及进行中的会员数据清除，暂停签核')
 if r.status!='draft':raise HTTPException(409,'已签核方案不可重复签核')
 r.status='approved';s.commit();audit(s,u,'签核触达方案 '+str(i));return dump(r)
@router.get('/outreaches/{i}/export')
def export_outreach(i:int,u=Depends(current),s=Depends(db)):
 r=s.get(Outreach,i)
 if not r:raise HTTPException(404,'触达方案不存在')
 _,locked=active_locks(s)
 if i in locked:raise HTTPException(409,'该方案涉及进行中的会员数据清除，暂停导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reader-outreach-{i}.json"'})
@router.post('/purges')
def purge_create(data:PurgeInput,u=Depends(editor),s=Depends(db)):
 reader=s.query(Reader).filter_by(code=data.code).first()
 if not reader:raise HTTPException(404,'会员档案不存在')
 impact=find_impact(s,data.code)
 p=Purge(reader_code=data.code,state='pending',impact=impact,progress={'phase':'待清理','done':0,'total':len(impact['segments'])+len(impact['outreaches'])+2},created=now(),updated=now())
 s.add(p)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'该会员已有进行中的清除任务')
 if not push('reader:purges',p.id):p.state='failed';p.error='队列暂不可用，请稍后重试';p.updated=now();s.commit();raise HTTPException(503,p.error)
 audit(s,u,'发起会员数据清除 '+data.code);return dump(p)
@router.get('/purges')
def purges(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Purge).order_by(Purge.id.desc()).limit(50).all()]
@router.get('/purges/{i}')
def purge_detail(i:int,u=Depends(current),s=Depends(db)):
 r=s.get(Purge,i)
 if not r:raise HTTPException(404,'清除任务不存在')
 return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
