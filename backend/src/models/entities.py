from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey,Index,text
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class Reader(Base):
 __tablename__='readers'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);weekly_reading=Column(Float);completion=Column(Float);night_share=Column(Float);note=Column(Text,default='')
class Segment(Base):
 __tablename__='segments'
 id=Column(Integer,primary_key=True);name=Column(String(100));k=Column(Integer);features=Column(JSON);state=Column(String(20),default='queued');result=Column(JSON);error=Column(Text,default='')
class Outreach(Base):
 __tablename__='outreaches'
 id=Column(Integer,primary_key=True);segment_id=Column(Integer,ForeignKey('segments.id'));cluster_id=Column(Integer);title=Column(String(100));note=Column(Text);members=Column(JSON);status=Column(String(20),default='draft')
class Purge(Base):
 __tablename__='purges'
 id=Column(Integer,primary_key=True);reader_code=Column(String(60),nullable=False);state=Column(String(20),default='pending');impact=Column(JSON);progress=Column(JSON);report=Column(JSON);error=Column(Text,default='');created=Column(String(40));updated=Column(String(40))
 __table_args__=(Index('purges_active_code','reader_code',unique=True,sqlite_where=text("state IN ('pending','running')"),postgresql_where=text("state IN ('pending','running')")),)
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
