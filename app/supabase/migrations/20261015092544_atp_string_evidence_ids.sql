-- Evidence/participant identifiers are opaque schema strings; UUIDs are the default,
-- but adapters may use deterministic string IDs scoped to their run/meeting.
alter table atp.sources alter column id type text using id::text;
alter table atp.facts alter column id type text using id::text;
alter table atp.participants alter column id type text using id::text;
