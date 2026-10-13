-- migrate:up

-- =============================================================================
-- Phase 1 base (docs/phase-1-design.md sections 3 and 4).
--
--   * Each database login may act only as certain actors: the worker as system
--     actors, n8n as `n8n`, the console as founders. Owners and superusers are
--     unrestricted (migrations, bootstrap, Jack's manual fixes).
--   * Systems: which systems exist, which are on, and which job types are theirs.
--   * Rules as data: settings, markets, need-states per market, vetting standards.
--   * Vetting evidence, console users and sessions.
--   * Job types may be namespaced: "<system>.<name>".
-- Conventions as in the first migration: every change writes an event, sensitive
-- columns are recorded as changed but never by value.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Which login is writing, and which actors it may claim
-- ---------------------------------------------------------------------------
-- The application group of the role making this change (current_user, which is
-- the SET ROLE target or the definer of a SECURITY DEFINER function) or of the
-- login (session_user). NULL for owners and superusers. Postgres reports a
-- superuser as having the privileges of every role, so superusers are skipped
-- first. USAGE (inherited privileges) rather than MEMBER, so a role holding only
-- ADMIN OPTION on a group (the owner that created it) is not counted as in it.
CREATE FUNCTION ledger_login_group() RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  u name;
BEGIN
  FOREACH u IN ARRAY ARRAY[current_user, session_user] LOOP
    CONTINUE WHEN (SELECT rolsuper FROM pg_roles WHERE rolname = u);
    IF pg_has_role(u, 'ledger_n8n', 'USAGE') THEN RETURN 'n8n'; END IF;
    IF pg_has_role(u, 'ledger_worker', 'USAGE') THEN RETURN 'worker'; END IF;
    IF pg_has_role(u, 'ledger_console', 'USAGE') THEN RETURN 'console'; END IF;
    IF pg_has_role(u, 'ledger_reporting', 'USAGE') THEN RETURN 'reporting'; END IF;
  END LOOP;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION ledger_current_actor_id() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_name   text := nullif(current_setting('app.actor', true), '');
  v_id     uuid;
  v_kind   text;
  v_group  text;
BEGIN
  IF v_name IS NULL THEN
    RAISE EXCEPTION 'app.actor is not set: every Ledger write must name its actor'
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'SELECT set_config(''app.actor'', ''<actor name>'', true) first';
  END IF;
  SELECT id, kind INTO v_id, v_kind FROM actors WHERE name = v_name AND active;
  IF v_id IS NULL THEN
    RAISE EXCEPTION 'unknown or inactive actor: %', v_name USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_group := ledger_login_group();
  IF v_group IS NOT NULL AND NOT (
       (v_group = 'n8n' AND v_name = 'n8n')
    OR (v_group = 'worker' AND v_kind = 'system' AND v_name NOT IN ('migration', 'review_console'))
    OR (v_group = 'console' AND (v_kind = 'founder' OR v_name = 'review_console'))
  ) THEN
    RAISE EXCEPTION 'this database login may not act as a % actor', v_kind
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN v_id;
END $$;

-- Founders change business rules. So does the `migration` actor, which only
-- owners and superusers can claim (migrations and the bootstrap).
CREATE FUNCTION ledger_is_rule_writer(p_actor uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM actors WHERE id = p_actor AND active
                   AND (kind = 'founder' OR name = 'migration'))
$$;

CREATE FUNCTION ledger_rule_writer_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT ledger_is_rule_writer(ledger_current_actor_id()) THEN
    RAISE EXCEPTION 'only a founder can change %', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Namespaced job types
-- ---------------------------------------------------------------------------
ALTER TABLE jobs DROP CONSTRAINT jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check
  CHECK (job_type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$');

-- ---------------------------------------------------------------------------
-- 3. Systems
-- ---------------------------------------------------------------------------
-- Filled from the code's manifests by the worker and the console at start-up.
-- New systems arrive turned off unless they are required; a founder turns them on.
CREATE TABLE systems (
  key                 text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
  title               text NOT NULL,
  version             integer NOT NULL CHECK (version >= 1),
  required            boolean NOT NULL DEFAULT false,
  enabled             boolean NOT NULL DEFAULT false,
  enabled_changed_by  uuid REFERENCES actors (id),
  enabled_changed_at  timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (enabled OR NOT required)
);

CREATE FUNCTION systems_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_actor uuid := ledger_current_actor_id();
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.enabled AND NOT NEW.enabled AND NEW.required THEN
    RAISE EXCEPTION 'system % is required and cannot be turned off', NEW.key
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.required THEN
    NEW.enabled := true;
  ELSIF TG_OP = 'INSERT' AND NOT ledger_is_rule_writer(v_actor) THEN
    NEW.enabled := false;
  ELSIF TG_OP = 'UPDATE' AND NEW.enabled IS DISTINCT FROM OLD.enabled
        AND NOT ledger_is_rule_writer(v_actor) THEN
    RAISE EXCEPTION 'only a founder can turn a system on or off' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'INSERT' OR NEW.enabled IS DISTINCT FROM OLD.enabled THEN
    NEW.enabled_changed_by := v_actor;
    NEW.enabled_changed_at := now();
  ELSE
    NEW.enabled_changed_by := OLD.enabled_changed_by;
    NEW.enabled_changed_at := OLD.enabled_changed_at;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER systems_enabled BEFORE INSERT OR UPDATE ON systems
  FOR EACH ROW EXECUTE FUNCTION systems_guard();

CREATE TABLE system_job_types (
  job_type    text PRIMARY KEY CHECK (job_type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$'),
  system_key  text NOT NULL REFERENCES systems (key),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 4. Settings (definitions and defaults live in code; values here)
-- ---------------------------------------------------------------------------
CREATE TABLE settings (
  key         text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'),
  value       jsonb NOT NULL,
  changed_by  uuid REFERENCES actors (id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION settings_stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.changed_by := ledger_current_actor_id();
  RETURN NEW;
END $$;
CREATE TRIGGER settings_rule_writer BEFORE INSERT OR UPDATE OR DELETE ON settings
  FOR EACH ROW EXECUTE FUNCTION ledger_rule_writer_guard();
CREATE TRIGGER settings_stamp BEFORE INSERT OR UPDATE ON settings
  FOR EACH ROW EXECUTE FUNCTION settings_stamp();

-- A setting's value, or the default the caller passes (the code's default).
CREATE FUNCTION ledger_setting(p_key text, p_default jsonb) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT coalesce((SELECT value FROM settings WHERE key = p_key), p_default)
$$;

-- ---------------------------------------------------------------------------
-- 5. Markets and need-states per market
-- ---------------------------------------------------------------------------
CREATE TABLE markets (
  key         text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*$'),
  name        text NOT NULL,
  time_zone   text NOT NULL,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION markets_time_zone_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = NEW.time_zone) THEN
    RAISE EXCEPTION 'unknown time zone %', NEW.time_zone USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER markets_rule_writer BEFORE INSERT OR UPDATE OR DELETE ON markets
  FOR EACH ROW EXECUTE FUNCTION ledger_rule_writer_guard();
CREATE TRIGGER markets_time_zone BEFORE INSERT OR UPDATE OF time_zone ON markets
  FOR EACH ROW EXECUTE FUNCTION markets_time_zone_guard();

ALTER TABLE need_states
  ADD COLUMN description  text,
  ADD COLUMN covers       text,
  ADD COLUMN not_covered  text,
  ADD COLUMN sort_order   smallint NOT NULL DEFAULT 100,
  DROP COLUMN launched_at;
CREATE TRIGGER need_states_rule_writer BEFORE INSERT OR UPDATE OR DELETE ON need_states
  FOR EACH ROW EXECUTE FUNCTION ledger_rule_writer_guard();

-- A row means: this need-state is marketed in this market ("launched").
-- Coaches may cover any active need-state; launch decides where ads go first and
-- what the readiness view counts toward the gate.
CREATE TABLE market_need_states (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  market_key      text NOT NULL REFERENCES markets (key),
  need_state_key  text NOT NULL REFERENCES need_states (key),
  launched        boolean NOT NULL DEFAULT true,
  changed_by      uuid REFERENCES actors (id),
  changed_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (market_key, need_state_key)
);

CREATE FUNCTION market_need_states_stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.launched IS DISTINCT FROM OLD.launched THEN
    NEW.changed_by := ledger_current_actor_id();
    NEW.changed_at := now();
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER market_need_states_rule_writer BEFORE INSERT OR UPDATE OR DELETE ON market_need_states
  FOR EACH ROW EXECUTE FUNCTION ledger_rule_writer_guard();
CREATE TRIGGER market_need_states_stamp BEFORE INSERT OR UPDATE ON market_need_states
  FOR EACH ROW EXECUTE FUNCTION market_need_states_stamp();

-- coaches, leads and ads belong to a market. With exactly one active market the
-- writer may leave it out; with more than one it must say which.
CREATE FUNCTION ledger_default_market() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_count  integer;
  v_key    text;
BEGIN
  IF NEW.market_key IS NULL THEN
    SELECT count(*), min(key) INTO v_count, v_key FROM markets WHERE active;
    IF v_count <> 1 THEN
      RAISE EXCEPTION '%.market_key is required when % markets are active', TG_TABLE_NAME, v_count
        USING ERRCODE = 'not_null_violation';
    END IF;
    NEW.market_key := v_key;
  END IF;
  RETURN NEW;
END $$;

ALTER TABLE coaches
  ADD COLUMN market_key           text NOT NULL REFERENCES markets (key),
  ADD COLUMN vetting_standard_id  uuid,
  ADD COLUMN postal_code          text CHECK (postal_code ~ '^[A-Za-z0-9 -]{3,10}$'),
  ADD COLUMN notes                text;
ALTER TABLE leads ADD COLUMN market_key text NOT NULL REFERENCES markets (key);
ALTER TABLE ads ADD COLUMN market_key text NOT NULL REFERENCES markets (key);
CREATE TRIGGER coaches_default_market BEFORE INSERT ON coaches
  FOR EACH ROW EXECUTE FUNCTION ledger_default_market();
CREATE TRIGGER leads_default_market BEFORE INSERT ON leads
  FOR EACH ROW EXECUTE FUNCTION ledger_default_market();
CREATE TRIGGER ads_default_market BEFORE INSERT ON ads
  FOR EACH ROW EXECUTE FUNCTION ledger_default_market();
CREATE INDEX coaches_market_status ON coaches (market_key, status);

-- ---------------------------------------------------------------------------
-- 6. Vetting standards as data
-- ---------------------------------------------------------------------------
CREATE TABLE vetting_standards (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version       integer NOT NULL UNIQUE CHECK (version >= 1),
  title         text NOT NULL,
  status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
  activated_by  uuid REFERENCES actors (id),
  activated_at  timestamptz,
  retired_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX vetting_standards_one_active ON vetting_standards (status) WHERE status = 'active';

CREATE TABLE vetting_item_types (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  standard_id    uuid NOT NULL REFERENCES vetting_standards (id),
  key            text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]*$'),
  label          text NOT NULL,
  description    text,
  evidence_kind  text NOT NULL DEFAULT 'any' CHECK (evidence_kind IN ('file', 'note', 'any')),
  required       boolean NOT NULL DEFAULT true,
  sort_order     smallint NOT NULL DEFAULT 100,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (standard_id, key)
);

-- A standard moves draft -> active -> retired (or draft -> retired, to discard
-- it). Only a draft can be edited. Only a founder activates one: that is how the
-- vetting standard gets decided (decisions item 2).
CREATE FUNCTION vetting_standards_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_actor     uuid := ledger_current_actor_id();
  v_required  integer;
BEGIN
  IF NOT ledger_is_rule_writer(v_actor) THEN
    RAISE EXCEPTION 'only a founder can change a vetting standard' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'vetting standards are never deleted; retire a draft instead'
      USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' THEN
      RAISE EXCEPTION 'a new vetting standard starts as a draft' USING ERRCODE = 'check_violation';
    END IF;
    NEW.activated_by := NULL;
    NEW.activated_at := NULL;
    NEW.retired_at := NULL;
    RETURN NEW;
  END IF;
  IF NEW.version <> OLD.version THEN
    RAISE EXCEPTION 'a vetting standard keeps its version number' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = OLD.status THEN
    IF OLD.status <> 'draft' AND NEW.title IS DISTINCT FROM OLD.title THEN
      RAISE EXCEPTION 'only a draft vetting standard can be edited' USING ERRCODE = 'check_violation';
    END IF;
    NEW.activated_by := OLD.activated_by;
    NEW.activated_at := OLD.activated_at;
    NEW.retired_at := OLD.retired_at;
    RETURN NEW;
  END IF;
  IF OLD.status = 'draft' AND NEW.status = 'active' THEN
    IF NOT ledger_is_founder(v_actor) THEN
      RAISE EXCEPTION 'only a founder can activate a vetting standard'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT count(*) INTO v_required FROM vetting_item_types WHERE standard_id = NEW.id AND required;
    IF v_required = 0 THEN
      RAISE EXCEPTION 'a vetting standard needs at least one required item before it can be active'
        USING ERRCODE = 'check_violation';
    END IF;
    NEW.activated_by := v_actor;
    NEW.activated_at := now();
  ELSIF NEW.status = 'retired' AND OLD.status IN ('draft', 'active') THEN
    NEW.retired_at := now();
  ELSE
    RAISE EXCEPTION 'a vetting standard moves draft -> active -> retired only'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER vetting_standards_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON vetting_standards
  FOR EACH ROW EXECUTE FUNCTION vetting_standards_guard();

CREATE FUNCTION vetting_item_types_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_status text;
BEGIN
  IF NOT ledger_is_rule_writer(ledger_current_actor_id()) THEN
    RAISE EXCEPTION 'only a founder can change a vetting standard' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    SELECT status INTO v_status FROM vetting_standards WHERE id = OLD.standard_id;
    IF v_status <> 'draft' THEN
      RAISE EXCEPTION 'items of an active or retired vetting standard are fixed'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.standard_id <> OLD.standard_id THEN
    RAISE EXCEPTION 'a vetting item type stays in its standard' USING ERRCODE = 'check_violation';
  END IF;
  SELECT status INTO v_status FROM vetting_standards WHERE id = NEW.standard_id;
  IF v_status <> 'draft' THEN
    RAISE EXCEPTION 'items of an active or retired vetting standard are fixed'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER vetting_item_types_draft_only BEFORE INSERT OR UPDATE OR DELETE ON vetting_item_types
  FOR EACH ROW EXECUTE FUNCTION vetting_item_types_guard();

ALTER TABLE coaches ADD CONSTRAINT coaches_vetting_standard_fk
  FOREIGN KEY (vetting_standard_id) REFERENCES vetting_standards (id);

-- vetting_items now refer to an item type of the coach's standard. item_type
-- (the key) is kept and filled from the type, so either can be given.
ALTER TABLE vetting_items DROP CONSTRAINT vetting_items_item_type_check;
ALTER TABLE vetting_items ADD COLUMN item_type_id uuid REFERENCES vetting_item_types (id);

CREATE FUNCTION vetting_items_type_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_standard  uuid;
  v_type      vetting_item_types%ROWTYPE;
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.item_type_id IS NOT DISTINCT FROM OLD.item_type_id
     AND NEW.item_type IS NOT DISTINCT FROM OLD.item_type AND NEW.coach_id = OLD.coach_id THEN
    RETURN NEW;
  END IF;
  SELECT vetting_standard_id INTO v_standard FROM coaches WHERE id = NEW.coach_id;
  IF v_standard IS NULL THEN
    RAISE EXCEPTION 'coach % has no vetting standard yet: move the coach to vetting first', NEW.coach_id
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.item_type_id IS NOT NULL THEN
    SELECT * INTO v_type FROM vetting_item_types WHERE id = NEW.item_type_id;
  ELSE
    SELECT * INTO v_type FROM vetting_item_types WHERE standard_id = v_standard AND key = NEW.item_type;
  END IF;
  IF v_type.id IS NULL OR v_type.standard_id <> v_standard THEN
    RAISE EXCEPTION 'vetting item % is not part of this coach''s standard', coalesce(NEW.item_type, '?')
      USING ERRCODE = 'check_violation';
  END IF;
  NEW.item_type_id := v_type.id;
  NEW.item_type := v_type.key;
  RETURN NEW;
END $$;
CREATE TRIGGER vetting_items_type BEFORE INSERT OR UPDATE ON vetting_items
  FOR EACH ROW EXECUTE FUNCTION vetting_items_type_guard();

-- A coach becomes active only by a founder's hand, under an active standard, with
-- every required item of that standard verified by a founder. A paused coach may
-- come back under the standard it was vetted on even if that has since been
-- retired. Entering vetting pins a standard: the active one, else the newest draft.
CREATE OR REPLACE FUNCTION coaches_status_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_actor            uuid := ledger_current_actor_id();
  v_missing          text[];
  v_standard_status  text;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.vetting_standard_id IS NOT NULL
     AND NEW.vetting_standard_id IS DISTINCT FROM OLD.vetting_standard_id
     AND NOT ledger_is_founder(v_actor) THEN
    RAISE EXCEPTION 'only a founder can move a coach to another vetting standard'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status IN ('vetting', 'active') AND NEW.vetting_standard_id IS NULL THEN
    SELECT id INTO NEW.vetting_standard_id FROM vetting_standards
     WHERE status IN ('active', 'draft')
     ORDER BY (status = 'active') DESC, version DESC LIMIT 1;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
    NEW.status_changed_by := OLD.status_changed_by;
    NEW.status_changed_at := OLD.status_changed_at;
    RETURN NEW;
  END IF;
  IF NEW.status = 'active' THEN
    IF NOT ledger_is_founder(v_actor) THEN
      RAISE EXCEPTION 'only a founder can mark a coach active' USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT status INTO v_standard_status FROM vetting_standards WHERE id = NEW.vetting_standard_id;
    IF v_standard_status IS DISTINCT FROM 'active'
       AND NOT (TG_OP = 'UPDATE' AND OLD.status = 'paused' AND v_standard_status = 'retired') THEN
      RAISE EXCEPTION 'coach % cannot be active: its vetting standard is not active', NEW.id
        USING ERRCODE = 'check_violation';
    END IF;
    SELECT array_agg(t.key ORDER BY t.sort_order, t.key) INTO v_missing
      FROM vetting_item_types t
     WHERE t.standard_id = NEW.vetting_standard_id AND t.required
       AND NOT EXISTS (
         SELECT 1 FROM vetting_items v
          WHERE v.coach_id = NEW.id AND v.item_type_id = t.id
            AND v.verified_at IS NOT NULL AND ledger_is_founder(v.verified_by));
    IF v_missing IS NOT NULL THEN
      RAISE EXCEPTION 'coach % cannot be active: unverified vetting items %', NEW.id, v_missing
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  NEW.status_changed_by := v_actor;
  NEW.status_changed_at := now();
  RETURN NEW;
END $$;

CREATE TABLE vetting_evidence (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vetting_item_id  uuid NOT NULL REFERENCES vetting_items (id),
  kind             text NOT NULL CHECK (kind IN ('file', 'note')),
  -- Relative to PRIVATE_DATA_DIR on the workstation; the file itself never leaves it.
  relative_path    text CHECK (relative_path !~ '^/' AND relative_path !~ '(^|/)\.\.(/|$)'),
  display_name     text,
  content_type     text,
  sha256           text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes       bigint CHECK (size_bytes >= 0),
  note             text,
  uploaded_by      uuid NOT NULL REFERENCES actors (id),
  removed_by       uuid REFERENCES actors (id),
  removed_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CHECK ((kind = 'file') = (relative_path IS NOT NULL AND sha256 IS NOT NULL AND size_bytes IS NOT NULL)),
  CHECK ((kind = 'note') = (note IS NOT NULL)),
  CHECK ((removed_by IS NULL) = (removed_at IS NULL))
);
CREATE INDEX vetting_evidence_item ON vetting_evidence (vetting_item_id);

CREATE FUNCTION vetting_evidence_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_actor uuid := ledger_current_actor_id();
BEGIN
  IF NOT ledger_is_founder(v_actor) THEN
    RAISE EXCEPTION 'only a founder handles vetting evidence' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'INSERT' THEN
    NEW.uploaded_by := v_actor;
    NEW.removed_by := NULL;
    NEW.removed_at := NULL;
    RETURN NEW;
  END IF;
  IF (NEW.kind, NEW.relative_path, NEW.sha256, NEW.size_bytes, NEW.note, NEW.vetting_item_id, NEW.uploaded_by)
     IS DISTINCT FROM
     (OLD.kind, OLD.relative_path, OLD.sha256, OLD.size_bytes, OLD.note, OLD.vetting_item_id, OLD.uploaded_by) THEN
    RAISE EXCEPTION 'evidence is never edited; add new evidence and remove the old'
      USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.removed_at IS NULL AND NEW.removed_at IS NOT NULL THEN
    NEW.removed_by := v_actor;
  ELSIF NEW.removed_at IS DISTINCT FROM OLD.removed_at THEN
    RAISE EXCEPTION 'removed evidence stays removed' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER vetting_evidence_founder BEFORE INSERT OR UPDATE ON vetting_evidence
  FOR EACH ROW EXECUTE FUNCTION vetting_evidence_guard();

-- ---------------------------------------------------------------------------
-- 7. Review console users and sessions
-- ---------------------------------------------------------------------------
CREATE TABLE console_users (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_id             uuid NOT NULL UNIQUE REFERENCES actors (id),
  tailscale_login      text UNIQUE,
  password_hash        text NOT NULL,
  allow_local_signin   boolean NOT NULL DEFAULT false,
  failed_signins       smallint NOT NULL DEFAULT 0 CHECK (failed_signins >= 0),
  locked_until         timestamptz,
  password_changed_at  timestamptz NOT NULL DEFAULT now(),
  active               boolean NOT NULL DEFAULT true,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION console_users_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM actors WHERE id = NEW.actor_id AND kind = 'founder') THEN
    RAISE EXCEPTION 'console users are founders' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER console_users_founder BEFORE INSERT OR UPDATE OF actor_id ON console_users
  FOR EACH ROW EXECUTE FUNCTION console_users_guard();

-- Telemetry-like and written on every request, so no row events; the console
-- writes sign-in, sign-out and lockout as domain events instead.
CREATE TABLE console_sessions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  console_user_id  uuid NOT NULL REFERENCES console_users (id),
  token_sha256     text NOT NULL UNIQUE CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
  local            boolean NOT NULL DEFAULT false,
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL,
  revoked_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX console_sessions_user ON console_sessions (console_user_id) WHERE revoked_at IS NULL;
CREATE TRIGGER console_sessions_updated_at BEFORE UPDATE ON console_sessions
  FOR EACH ROW EXECUTE FUNCTION ledger_touch_updated_at();

-- ---------------------------------------------------------------------------
-- 8. Events and updated_at for the new tables; coaches gains a sensitive column
-- ---------------------------------------------------------------------------
DROP TRIGGER coaches_events ON coaches;
CREATE TRIGGER coaches_events AFTER INSERT OR UPDATE OR DELETE ON coaches
  FOR EACH ROW EXECUTE FUNCTION ledger_log_change('id', 'legal_name', 'email', 'phone', 'notes');

DO $$
DECLARE
  spec jsonb := '{
    "systems":             ["key"],
    "system_job_types":    ["job_type"],
    "settings":            ["key"],
    "markets":             ["key"],
    "market_need_states":  ["id"],
    "vetting_standards":   ["id"],
    "vetting_item_types":  ["id"],
    "vetting_evidence":    ["id", "relative_path", "display_name", "note"],
    "console_users":       ["id", "tailscale_login", "password_hash"]
  }';
  t text;
  args text;
BEGIN
  FOR t IN SELECT jsonb_object_keys(spec) LOOP
    SELECT string_agg(quote_literal(v), ', ') INTO args FROM jsonb_array_elements_text(spec -> t) AS v;
    EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I
                    FOR EACH ROW EXECUTE FUNCTION ledger_log_change(%s)', t || '_events', t, args);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON %I
                    FOR EACH ROW EXECUTE FUNCTION ledger_touch_updated_at()', t || '_updated_at', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 9. Queue health reads its thresholds from settings and ignores paused systems
-- ---------------------------------------------------------------------------
DROP FUNCTION api.queue_health(numeric, integer, integer);

-- What the stale-job alert reads. IDs and numbers only.
-- Threshold and repeat interval come from settings core.stale_job_alert_hours
-- (default 4, decisions item 24) and core.stale_job_alert_repeat_minutes (240).
-- should_alert is true on the check that first finds the queue stale, then once
-- every repeat interval while it stays stale. Jobs of systems that are turned off
-- are waiting on purpose: they are counted separately and never alert.
CREATE FUNCTION api.queue_health(p_check_every_min integer DEFAULT 15)
RETURNS TABLE (
  oldest_pending_job_id     uuid,
  oldest_pending_job_type   text,
  oldest_pending_age_min    integer,
  pending_count             integer,
  paused_pending_count      integer,
  failed_last_24h           integer,
  newest_heartbeat_age_min  integer,
  threshold_hours           numeric,
  is_stale                  boolean,
  should_alert              boolean
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  WITH cfg AS (
    SELECT (ledger_setting('core.stale_job_alert_hours', '4') #>> '{}')::numeric AS hours,
           (ledger_setting('core.stale_job_alert_repeat_minutes', '240') #>> '{}')::integer AS repeat_min
  ), paused AS (
    SELECT t.job_type FROM system_job_types t JOIN systems s ON s.key = t.system_key WHERE NOT s.enabled
  ), oldest AS (
    SELECT id, job_type, (extract(epoch FROM now() - run_after) / 60)::integer AS age_min
      FROM jobs
     WHERE status = 'pending' AND run_after <= now()
       AND job_type NOT IN (SELECT job_type FROM paused)
     ORDER BY run_after LIMIT 1
  ), hb AS (
    SELECT max(last_seen_at) AS last_seen FROM worker_heartbeats
  ), s AS (
    SELECT o.*, hb.last_seen, cfg.hours, (cfg.hours * 60)::integer AS threshold_min, cfg.repeat_min
      FROM cfg CROSS JOIN hb LEFT JOIN oldest o ON true
  )
  SELECT s.id, s.job_type, s.age_min,
         (SELECT count(*) FROM jobs WHERE status = 'pending'
             AND job_type NOT IN (SELECT job_type FROM paused))::integer,
         (SELECT count(*) FROM jobs WHERE status = 'pending'
             AND job_type IN (SELECT job_type FROM paused))::integer,
         (SELECT count(*) FROM jobs WHERE status = 'failed' AND finished_at > now() - interval '24 hours')::integer,
         (extract(epoch FROM now() - s.last_seen) / 60)::integer,
         s.hours,
         coalesce(s.age_min >= s.threshold_min, false),
         coalesce(s.age_min >= s.threshold_min
                  AND (s.age_min - s.threshold_min) % greatest(s.repeat_min, 1) < p_check_every_min,
                  false)
    FROM s
$$;
REVOKE ALL ON FUNCTION api.queue_health(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION api.queue_health(integer) TO ledger_n8n;

-- ---------------------------------------------------------------------------
-- 10. Privileges on the new tables
-- ---------------------------------------------------------------------------
GRANT SELECT ON systems, system_job_types, settings, markets, market_need_states,
                vetting_standards, vetting_item_types
  TO ledger_worker, ledger_console, ledger_reporting;
GRANT SELECT ON vetting_evidence TO ledger_console, ledger_reporting;
GRANT INSERT, UPDATE ON systems, system_job_types TO ledger_worker, ledger_console;
GRANT INSERT, UPDATE ON settings, markets, market_need_states, vetting_standards,
                        vetting_item_types, vetting_evidence
  TO ledger_console;
GRANT DELETE ON vetting_item_types, coach_need_states TO ledger_console;
-- Console logins and sessions are for the console alone.
GRANT SELECT, INSERT, UPDATE ON console_users, console_sessions TO ledger_console;

-- migrate:down

REVOKE ALL ON console_users, console_sessions FROM ledger_console;
REVOKE DELETE ON coach_need_states FROM ledger_console;

DROP FUNCTION api.queue_health(integer);
CREATE FUNCTION api.queue_health(
  p_threshold_hours  numeric,
  p_check_every_min  integer DEFAULT 15,
  p_repeat_minutes   integer DEFAULT 240
)
RETURNS TABLE (
  oldest_pending_job_id     uuid,
  oldest_pending_job_type   text,
  oldest_pending_age_min    integer,
  pending_count             integer,
  failed_last_24h           integer,
  newest_heartbeat_age_min  integer,
  is_stale                  boolean,
  should_alert              boolean
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  WITH oldest AS (
    SELECT id, job_type, run_after,
           (extract(epoch FROM now() - run_after) / 60)::integer AS age_min
      FROM jobs
     WHERE status = 'pending' AND run_after <= now()
     ORDER BY run_after LIMIT 1
  ), hb AS (
    SELECT max(last_seen_at) AS last_seen FROM worker_heartbeats
  ), s AS (
    SELECT o.*, hb.last_seen, (p_threshold_hours * 60)::integer AS threshold_min
      FROM hb LEFT JOIN oldest o ON true
  )
  SELECT s.id, s.job_type, s.age_min,
         (SELECT count(*) FROM jobs WHERE status = 'pending')::integer,
         (SELECT count(*) FROM jobs WHERE status = 'failed' AND finished_at > now() - interval '24 hours')::integer,
         (extract(epoch FROM now() - s.last_seen) / 60)::integer,
         coalesce(s.age_min >= s.threshold_min, false),
         coalesce(s.age_min >= s.threshold_min
                  AND (s.age_min - s.threshold_min) % greatest(p_repeat_minutes, 1) < p_check_every_min,
                  false)
    FROM s
$$;
REVOKE ALL ON FUNCTION api.queue_health(numeric, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION api.queue_health(numeric, integer, integer) TO ledger_n8n;

CREATE OR REPLACE FUNCTION coaches_status_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_actor    uuid := ledger_current_actor_id();
  v_missing  text[];
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
    NEW.status_changed_by := OLD.status_changed_by;
    NEW.status_changed_at := OLD.status_changed_at;
    RETURN NEW;
  END IF;
  IF NEW.status = 'active' THEN
    IF NOT ledger_is_founder(v_actor) THEN
      RAISE EXCEPTION 'only a founder can mark a coach active' USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT array_agg(t ORDER BY t) INTO v_missing
      FROM unnest(ARRAY['interview', 'reference_1', 'reference_2', 'paying_client_evidence',
                        'program_and_price', 'signed_agreement']) AS t
     WHERE NOT EXISTS (
       SELECT 1 FROM vetting_items v
        WHERE v.coach_id = NEW.id AND v.item_type = t
          AND v.verified_at IS NOT NULL AND ledger_is_founder(v.verified_by));
    IF v_missing IS NOT NULL THEN
      RAISE EXCEPTION 'coach % cannot be active: unverified vetting items %', NEW.id, v_missing
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  NEW.status_changed_by := v_actor;
  NEW.status_changed_at := now();
  RETURN NEW;
END $$;

DROP TRIGGER coaches_events ON coaches;
CREATE TRIGGER coaches_events AFTER INSERT OR UPDATE OR DELETE ON coaches
  FOR EACH ROW EXECUTE FUNCTION ledger_log_change('id', 'legal_name', 'email', 'phone');

DROP TRIGGER vetting_items_type ON vetting_items;
DROP FUNCTION vetting_items_type_guard();
ALTER TABLE vetting_items DROP COLUMN item_type_id;
ALTER TABLE vetting_items ADD CONSTRAINT vetting_items_item_type_check
  CHECK (item_type IN ('interview', 'reference_1', 'reference_2', 'paying_client_evidence',
                       'program_and_price', 'signed_agreement'));

DROP TRIGGER coaches_default_market ON coaches;
DROP TRIGGER leads_default_market ON leads;
DROP TRIGGER ads_default_market ON ads;
DROP INDEX coaches_market_status;
ALTER TABLE coaches DROP CONSTRAINT coaches_vetting_standard_fk;
ALTER TABLE coaches DROP COLUMN market_key, DROP COLUMN vetting_standard_id,
                    DROP COLUMN postal_code, DROP COLUMN notes;
ALTER TABLE leads DROP COLUMN market_key;
ALTER TABLE ads DROP COLUMN market_key;

DROP TRIGGER need_states_rule_writer ON need_states;
ALTER TABLE need_states
  DROP COLUMN description, DROP COLUMN covers, DROP COLUMN not_covered, DROP COLUMN sort_order,
  ADD COLUMN launched_at timestamptz;

DROP TABLE console_sessions, console_users, vetting_evidence, vetting_item_types, vetting_standards,
           market_need_states, markets, settings, system_job_types, systems;

DROP FUNCTION console_users_guard(), vetting_evidence_guard(), vetting_item_types_guard(),
              vetting_standards_guard(), ledger_default_market(), market_need_states_stamp(),
              markets_time_zone_guard(), ledger_setting(text, jsonb), settings_stamp(),
              systems_guard(), ledger_rule_writer_guard(), ledger_is_rule_writer(uuid);

ALTER TABLE jobs DROP CONSTRAINT jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check CHECK (job_type ~ '^[a-z][a-z0-9_]*$');

CREATE OR REPLACE FUNCTION ledger_current_actor_id() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_name text := nullif(current_setting('app.actor', true), '');
  v_id   uuid;
BEGIN
  IF v_name IS NULL THEN
    RAISE EXCEPTION 'app.actor is not set: every Ledger write must name its actor'
      USING ERRCODE = 'insufficient_privilege',
            HINT = 'SELECT set_config(''app.actor'', ''<actor name>'', true) first';
  END IF;
  SELECT id INTO v_id FROM actors WHERE name = v_name AND active;
  IF v_id IS NULL THEN
    RAISE EXCEPTION 'unknown or inactive actor: %', v_name USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN v_id;
END $$;
DROP FUNCTION ledger_login_group();
