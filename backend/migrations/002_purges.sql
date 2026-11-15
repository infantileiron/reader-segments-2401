-- 002_purges.sql 会员数据永久清除任务表
-- 部署时由 ORM create_all 自动建表；本文件保留实际 DDL 备查。
-- purges_active_code 部分唯一索引：同一会员最多一个 pending/running 任务，并发重复发起由数据库拒绝。

CREATE TABLE public.purges (
    id integer NOT NULL,
    reader_code character varying(60) NOT NULL,
    state character varying(20),
    impact json,
    progress json,
    report json,
    error text,
    created character varying(40),
    updated character varying(40)
);

CREATE SEQUENCE public.purges_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.purges_id_seq OWNED BY public.purges.id;

ALTER TABLE ONLY public.purges ALTER COLUMN id SET DEFAULT nextval('public.purges_id_seq'::regclass);

ALTER TABLE ONLY public.purges
    ADD CONSTRAINT purges_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX purges_active_code ON public.purges (reader_code)
    WHERE state IN ('pending', 'running');
