-- ═══════════════════════════════════════════════════════════════
-- Run this entire file in your Supabase SQL editor
-- ═══════════════════════════════════════════════════════════════

-- 0. Drop everything safely (handles missing tables gracefully)
DO $$ BEGIN
  drop trigger if exists on_agent_created on agents;
  drop trigger if exists on_agent_deleted on agents;
  drop trigger if exists on_schedule_created on agent_schedule;
  drop trigger if exists on_blog_generated on generated_blogs;
EXCEPTION WHEN others THEN NULL;
END $$;

drop trigger if exists on_auth_user_created on auth.users;
drop function if exists handle_new_user() cascade;
drop function if exists increment_agent_count() cascade;
drop function if exists decrement_agent_count() cascade;
drop function if exists increment_posts_scheduled() cascade;
drop function if exists increment_blogs_generated() cascade;
drop table if exists generated_blogs cascade;
drop table if exists agent_schedule cascade;
drop table if exists agents cascade;
drop table if exists user_stats cascade;
drop table if exists profiles cascade;


-- 1. Profiles
create table if not exists profiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  name       text not null,
  created_at timestamptz default now()
);

-- 2. User Stats — must exist BEFORE the trigger function
create table if not exists user_stats (
  user_id               uuid primary key references auth.users(id) on delete cascade,
  agent_count           int  not null default 0,
  agent_limit           int  not null default 3,
  total_blogs_generated int  not null default 0,
  total_posts_scheduled int  not null default 0,
  updated_at            timestamptz default now()
);

-- 3. Agents
create table if not exists agents (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users(id) on delete cascade,
  name              text not null default 'My Blog Agent',
  scenario          text not null default 'themed',
  website_url       text,
  themes            text[],
  tone              text default 'professional',
  audience          text default 'general audience',
  language          text default 'English',
  duration_months   numeric not null default 1 check (duration_months between 0.5 and 12),
  frequency         text not null default 'weekly',
  content_length    text default 'medium',
  brand_name        text,
  brand_description text,
  status            text default 'active',
  created_at        timestamptz default now()
);

-- 4. Agent Schedule
create table agent_schedule (
  id             uuid primary key default gen_random_uuid(),
  agent_id       uuid not null references agents(id) on delete cascade,
  user_id        uuid not null references auth.users(id) on delete cascade,
  scheduled_date date not null,
  title          text not null,
  description    text,
  keywords       text[],
  word_count     int default 800,
  status         text default 'pending',
  created_at     timestamptz default now()
);

-- 5. Generated Blogs
create table generated_blogs (
  id                   uuid primary key default gen_random_uuid(),
  agent_id             uuid not null references agents(id) on delete cascade,
  user_id              uuid not null references auth.users(id) on delete cascade,
  schedule_id          uuid references agent_schedule(id) on delete set null,
  scheduled_date       date not null,
  title                text not null,
  meta_description     text,
  content              text,
  tags                 text[],
  reading_time_minutes int default 5,
  created_at           timestamptz default now()
);


-- ═══════════════════════════════════════════════════════════════
-- Triggers
-- ═══════════════════════════════════════════════════════════════

-- Auto-create profile + user_stats on signup
create or replace function handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into profiles (id, name)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'name', split_part(new.email, '@', 1))
  ) on conflict (id) do nothing;

  insert into user_stats (user_id)
  values (new.id)
  on conflict (user_id) do nothing;

  return new;
exception when others then
  return new; -- never block signup
end;
$$;

create trigger on_auth_user_created



  after insert on auth.users
  for each row execute procedure handle_new_user();

-- Agent count triggers
create or replace function increment_agent_count()
returns trigger language plpgsql security definer as $$
begin
  insert into user_stats (user_id, agent_count) values (new.user_id, 1)
  on conflict (user_id) do update
  set agent_count = user_stats.agent_count + 1, updated_at = now();
  return new;
end;
$$;

create trigger on_agent_created
  after insert on agents
  for each row execute procedure increment_agent_count();

create or replace function decrement_agent_count()
returns trigger language plpgsql security definer as $$
begin
  update user_stats
  set agent_count = greatest(0, agent_count - 1), updated_at = now()
  where user_id = old.user_id;
  return old;
end;
$$;

create trigger on_agent_deleted
  after delete on agents
  for each row execute procedure decrement_agent_count();

create or replace function increment_posts_scheduled()
returns trigger language plpgsql security definer as $$
begin
  insert into user_stats (user_id, total_posts_scheduled) values (new.user_id, 1)
  on conflict (user_id) do update
  set total_posts_scheduled = user_stats.total_posts_scheduled + 1, updated_at = now();
  return new;
end;
$$;

create trigger on_schedule_created
  after insert on agent_schedule
  for each row execute procedure increment_posts_scheduled();

create or replace function increment_blogs_generated()
returns trigger language plpgsql security definer as $$
begin
  insert into user_stats (user_id, total_blogs_generated) values (new.user_id, 1)
  on conflict (user_id) do update
  set total_blogs_generated = user_stats.total_blogs_generated + 1, updated_at = now();
  return new;
end;
$$;

create trigger on_blog_generated
  after insert on generated_blogs
  for each row execute procedure increment_blogs_generated();


-- ═══════════════════════════════════════════════════════════════
-- Indexes
-- ═══════════════════════════════════════════════════════════════

create index on agents(user_id);
create index on agents(status);
create index on agent_schedule(agent_id, scheduled_date);
create index on agent_schedule(user_id);
create index on generated_blogs(agent_id);
create index on generated_blogs(user_id);
create index on generated_blogs(agent_id, scheduled_date);


-- ═══════════════════════════════════════════════════════════════
-- Row Level Security
-- ═══════════════════════════════════════════════════════════════

alter table profiles        enable row level security;
alter table user_stats      enable row level security;
alter table agents          enable row level security;
alter table agent_schedule  enable row level security;
alter table generated_blogs enable row level security;

create policy "profiles: own row"   on profiles        for all using (auth.uid() = id);
create policy "user_stats: own row" on user_stats      for all using (auth.uid() = user_id);
create policy "agents: own rows"    on agents          for all using (auth.uid() = user_id);
create policy "schedule: own rows"  on agent_schedule  for all using (auth.uid() = user_id);
create policy "blogs: own rows"     on generated_blogs for all using (auth.uid() = user_id);


