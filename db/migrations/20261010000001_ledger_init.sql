-- migrate:up

-- =============================================================================
-- Chosen Local Ledger, first migration.
--
-- Conventions (see docs/phase-0-design.md section 5):
--   * uuid primary keys, created_at / updated_at on every table (append-only
--     tables have no updated_at because their rows never change).
--   * Every insert, update and delete writes an `events` row through
--     ledger_log_change(). The writer must identify itself first:
--         SELECT set_config('app.actor', 'worker', true);
--     No actor, no write. Optional context: app.job_id, app.prompt_version,
--     app.model, app.approval_id.
--   * Columns holding personal or call-derived data are passed to the trigger
--     as "sensitive": the event records that they changed, never their value.
--   * Money is integer cents. Timestamps are timestamptz (UTC).
--   * Status columns are text with CHECK constraints, so adding a value is a
--     one-line migration.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Group roles. They cannot log in. Login users are created on each server
-- with a password Jack sets there (deploy/README.md), then granted one of these.
-- ---------------------------------------------------------------------------
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['ledger_worker', 'ledger_console', 'ledger_n8n', 'ledger_reporting'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Shared trigger functions
-- ---------------------------------------------------------------------------
CREATE FUNCTION ledger_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

CREATE FUNCTION ledger_forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

-- ---------------------------------------------------------------------------
-- Reference
-- ---------------------------------------------------------------------------
CREATE TABLE actors (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        text NOT NULL CHECK (kind IN ('founder', 'system', 'coach', 'lead', 'external')),
  name        text NOT NULL UNIQUE,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Seeded before the events trigger is attached, so the first writer exists.
INSERT INTO actors (kind, name) VALUES
  ('founder', 'sam'),
  ('founder', 'jack'),
  ('system',  'migration'),
  ('system',  'worker'),
  ('system',  'n8n'),
  ('system',  'review_console'),
  ('system',  'eval');

CREATE FUNCTION ledger_current_actor_id() RETURNS uuid
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

CREATE FUNCTION ledger_is_founder(p_actor uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM actors WHERE id = p_actor AND kind = 'founder' AND active)
$$;

CREATE FUNCTION ledger_require_founder(p_actor uuid, p_what text) RETURNS void
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF p_actor IS NOT NULL AND NOT ledger_is_founder(p_actor) THEN
    RAISE EXCEPTION '% must be a founder', p_what USING ERRCODE = 'check_violation';
  END IF;
END $$;

CREATE TABLE need_states (
  key          text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*$'),
  label        text NOT NULL,
  active       boolean NOT NULL DEFAULT true,
  launched_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Operations: jobs, approvals, events
-- ---------------------------------------------------------------------------
CREATE TABLE jobs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type          text NOT NULL CHECK (job_type ~ '^[a-z][a-z0-9_]*$'),
  payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
  entity_type       text,
  entity_id         uuid,
  status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
  priority          smallint NOT NULL DEFAULT 100,
  run_after         timestamptz NOT NULL DEFAULT now(),
  attempts          smallint NOT NULL DEFAULT 0,
  max_attempts      smallint NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
  claimed_by        text,
  claimed_at        timestamptz,
  lease_expires_at  timestamptz,
  idempotency_key   text UNIQUE,
  result_ref        jsonb,
  error_class       text,
  error_detail      text CHECK (length(error_detail) <= 500),
  finished_at       timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_claimable ON jobs (priority, run_after) WHERE status = 'pending';
CREATE INDEX jobs_running_lease ON jobs (lease_expires_at) WHERE status = 'running';
CREATE INDEX jobs_entity ON jobs (entity_type, entity_id);

CREATE TABLE approvals (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind           text NOT NULL
                   CHECK (kind IN ('message', 'outreach', 'invoice', 'match', 'template', 'spend', 'safety_clear')),
  subject_type   text NOT NULL,
  subject_id     uuid NOT NULL,
  proposed_by    uuid NOT NULL REFERENCES actors (id),
  proposal_ref   text,
  proposal_hash  text,
  decided_by     uuid REFERENCES actors (id),
  decision       text CHECK (decision IN ('approved', 'rejected', 'edited')),
  decision_note  text,
  decided_at     timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CHECK ((decision IS NULL) = (decided_by IS NULL) AND (decision IS NULL) = (decided_at IS NULL))
);
CREATE INDEX approvals_subject ON approvals (subject_type, subject_id);

CREATE TABLE events (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
  entity_type     text NOT NULL,
  entity_id       text NOT NULL,
  event_type      text NOT NULL,
  actor_id        uuid NOT NULL REFERENCES actors (id),
  job_id          uuid REFERENCES jobs (id),
  approval_id     uuid REFERENCES approvals (id),
  prompt_version  text,
  model           text,
  input_ref       jsonb,
  output_ref      jsonb,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()  -- never changes: rows are append-only
);
CREATE INDEX events_entity ON events (entity_type, entity_id, id);
CREATE INDEX events_job ON events (job_id) WHERE job_id IS NOT NULL;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION ledger_forbid_change();
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
  FOR EACH STATEMENT EXECUTE FUNCTION ledger_forbid_change();

-- The generic change logger.
--   TG_ARGV[0]   primary key column
--   TG_ARGV[1..] sensitive columns: recorded as changed, value never stored
-- Updates that only move updated_at or lease_expires_at (lease renewals) are
-- not logged; everything else is.
CREATE FUNCTION ledger_log_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_actor      uuid := ledger_current_actor_id();
  v_pk         text := TG_ARGV[0];
  v_sensitive  text[] := coalesce(TG_ARGV[1:], ARRAY[]::text[]);
  v_noise      text[] := ARRAY['updated_at', 'created_at', 'lease_expires_at'];
  v_new        jsonb;
  v_old        jsonb;
  v_changed    text[];
  v_values     jsonb := '{}'::jsonb;
  v_col        text;
  v_entity_id  text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    v_new := to_jsonb(NEW);
    SELECT array_agg(k ORDER BY k) INTO v_changed
      FROM jsonb_each(v_new) AS e(k, v)
     WHERE v <> 'null'::jsonb AND NOT (k = ANY (v_noise));
    v_entity_id := v_new ->> v_pk;
  ELSIF TG_OP = 'UPDATE' THEN
    v_new := to_jsonb(NEW);
    v_old := to_jsonb(OLD);
    SELECT array_agg(k ORDER BY k) INTO v_changed
      FROM jsonb_each(v_new) AS e(k, v)
     WHERE v IS DISTINCT FROM v_old -> k AND NOT (k = ANY (v_noise));
    IF v_changed IS NULL THEN
      RETURN NEW;
    END IF;
    v_entity_id := v_new ->> v_pk;
  ELSE
    v_old := to_jsonb(OLD);
    v_entity_id := v_old ->> v_pk;
  END IF;

  IF v_changed IS NOT NULL THEN
    FOREACH v_col IN ARRAY v_changed LOOP
      IF NOT (v_col = ANY (v_sensitive)) THEN
        v_values := v_values || jsonb_build_object(v_col, v_new -> v_col);
      END IF;
    END LOOP;
  END IF;

  INSERT INTO events (entity_type, entity_id, event_type, actor_id, job_id, approval_id,
                      prompt_version, model, payload)
  VALUES (
    TG_TABLE_NAME,
    v_entity_id,
    'row.' || lower(TG_OP),
    v_actor,
    nullif(current_setting('app.job_id', true), '')::uuid,
    nullif(current_setting('app.approval_id', true), '')::uuid,
    nullif(current_setting('app.prompt_version', true), ''),
    nullif(current_setting('app.model', true), ''),
    jsonb_strip_nulls(jsonb_build_object(
      'changed', to_jsonb(coalesce(v_changed, ARRAY[]::text[])),
      'values', v_values,
      'sensitive_changed', (
        SELECT to_jsonb(array_agg(c)) FROM unnest(coalesce(v_changed, ARRAY[]::text[])) AS c
         WHERE c = ANY (v_sensitive))
    ))
  );

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END $$;

-- Domain events (e.g. 'match.confirmed') on top of the row events.
CREATE FUNCTION ledger_emit(
  p_entity_type text, p_entity_id text, p_event_type text,
  p_payload jsonb DEFAULT '{}'::jsonb, p_input_ref jsonb DEFAULT NULL, p_output_ref jsonb DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE v_id bigint;
BEGIN
  INSERT INTO events (entity_type, entity_id, event_type, actor_id, job_id, approval_id,
                      prompt_version, model, input_ref, output_ref, payload)
  VALUES (p_entity_type, p_entity_id, p_event_type, ledger_current_actor_id(),
          nullif(current_setting('app.job_id', true), '')::uuid,
          nullif(current_setting('app.approval_id', true), '')::uuid,
          nullif(current_setting('app.prompt_version', true), ''),
          nullif(current_setting('app.model', true), ''),
          p_input_ref, p_output_ref, coalesce(p_payload, '{}'::jsonb))
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

CREATE TABLE worker_heartbeats (
  worker_id       text PRIMARY KEY,
  host            text NOT NULL,
  git_sha         text,
  started_at      timestamptz NOT NULL,
  last_seen_at    timestamptz NOT NULL,
  current_job_id  uuid REFERENCES jobs (id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
-- Heartbeats are telemetry written every 30 s; they are deliberately not
-- logged to events (start and stop are logged by the worker as domain events).

-- ---------------------------------------------------------------------------
-- Ads (before leads, which reference them)
-- ---------------------------------------------------------------------------
CREATE TABLE ads (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  platform        text NOT NULL CHECK (platform IN ('meta', 'tiktok')),
  platform_ad_id  text NOT NULL,
  campaign_id     text,
  adset_id        text,
  name            text,
  need_state_key  text REFERENCES need_states (key),
  status          text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (platform, platform_ad_id)
);

CREATE TABLE ad_daily (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ad_id           uuid NOT NULL REFERENCES ads (id),
  date            date NOT NULL,
  spend_cents     integer NOT NULL DEFAULT 0 CHECK (spend_cents >= 0),
  impressions     integer NOT NULL DEFAULT 0 CHECK (impressions >= 0),
  clicks          integer NOT NULL DEFAULT 0 CHECK (clicks >= 0),
  platform_leads  integer NOT NULL DEFAULT 0 CHECK (platform_leads >= 0),
  imported_at     timestamptz NOT NULL DEFAULT now(),
  import_run_id   text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (ad_id, date)
);

-- ---------------------------------------------------------------------------
-- Leads, consent, bookings
-- ---------------------------------------------------------------------------
CREATE TABLE leads (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  first_name               text,
  phone_e164               text CHECK (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  email                    text,
  need_state_key           text REFERENCES need_states (key),
  budget_band              text,
  income_band              text,
  start_timeframe          text,
  intake_note              text,
  submitted_at             timestamptz,
  source_platform          text CHECK (source_platform IN ('meta', 'tiktok', 'organic', 'referral', 'test')),
  ad_id                    uuid REFERENCES ads (id),
  utm_source               text,
  utm_medium               text,
  utm_campaign             text,
  utm_content              text,
  click_id                 text,
  landing_variant          text,
  close_lead_id            text UNIQUE,
  dedupe_key               text,
  merged_into_lead_id      uuid REFERENCES leads (id),
  status                   text NOT NULL DEFAULT 'new'
                             CHECK (status IN ('new', 'booked', 'called', 'qualified', 'disqualified',
                                               'matched', 'introduced', 'won', 'lost', 'dormant')),
  qualification_result     text CHECK (qualification_result IN ('qualified', 'not_qualified')),
  qualification_reason     text CHECK (qualification_reason ~ '^[a-z][a-z0-9_]*$'),
  prescore                 smallint,
  first_contact_at         timestamptz,
  consent_call             boolean NOT NULL DEFAULT false,
  consent_text             boolean NOT NULL DEFAULT false,
  consent_call_at          timestamptz,
  consent_text_at          timestamptz,
  consent_wording_version  text,
  safety_hold              boolean NOT NULL DEFAULT false,
  safety_hold_set_by       uuid REFERENCES actors (id),
  safety_hold_set_at       timestamptz,
  safety_hold_cleared_by   uuid REFERENCES actors (id),
  safety_hold_cleared_at   timestamptz,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX leads_dedupe ON leads (dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX leads_status ON leads (status);

-- Safety holds: anyone may set one; only a founder may clear one.
CREATE FUNCTION leads_safety_hold_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_actor uuid := ledger_current_actor_id();
BEGIN
  IF NEW.safety_hold AND (TG_OP = 'INSERT' OR NOT OLD.safety_hold) THEN
    NEW.safety_hold_set_by := v_actor;
    NEW.safety_hold_set_at := now();
    NEW.safety_hold_cleared_by := NULL;
    NEW.safety_hold_cleared_at := NULL;
  ELSIF TG_OP = 'UPDATE' AND OLD.safety_hold AND NOT NEW.safety_hold THEN
    IF NOT ledger_is_founder(v_actor) THEN
      RAISE EXCEPTION 'only a founder can clear a safety hold' USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.safety_hold_cleared_by := v_actor;
    NEW.safety_hold_cleared_at := now();
  ELSIF TG_OP = 'UPDATE' THEN
    -- The hold's audit columns move only with the hold itself.
    NEW.safety_hold_set_by := OLD.safety_hold_set_by;
    NEW.safety_hold_set_at := OLD.safety_hold_set_at;
    NEW.safety_hold_cleared_by := OLD.safety_hold_cleared_by;
    NEW.safety_hold_cleared_at := OLD.safety_hold_cleared_at;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER leads_safety_hold BEFORE INSERT OR UPDATE ON leads
  FOR EACH ROW EXECUTE FUNCTION leads_safety_hold_guard();

CREATE TABLE consent_records (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id          uuid NOT NULL REFERENCES leads (id),
  channel          text NOT NULL CHECK (channel IN ('call', 'text', 'email', 'recording', 'coach_share')),
  action           text NOT NULL CHECK (action IN ('granted', 'revoked')),
  source           text NOT NULL CHECK (source IN ('intake_form', 'sms_reply', 'call', 'founder')),
  wording_version  text,
  evidence_ref     text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()  -- never changes: rows are append-only
);
CREATE INDEX consent_records_lead ON consent_records (lead_id, created_at);
CREATE TRIGGER consent_records_append_only BEFORE UPDATE OR DELETE ON consent_records
  FOR EACH ROW EXECUTE FUNCTION ledger_forbid_change();

CREATE TABLE bookings (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id             uuid NOT NULL REFERENCES leads (id),
  calcom_booking_uid  text UNIQUE,
  scheduled_for       timestamptz NOT NULL,
  status              text NOT NULL DEFAULT 'booked'
                        CHECK (status IN ('booked', 'rescheduled', 'cancelled', 'attended', 'no_show')),
  status_changed_at   timestamptz NOT NULL DEFAULT now(),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX bookings_lead ON bookings (lead_id);

-- ---------------------------------------------------------------------------
-- Calls (content stays on the workstation; only paths and structured values here)
-- ---------------------------------------------------------------------------
CREATE TABLE calls (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id              uuid NOT NULL REFERENCES leads (id),
  booking_id           uuid REFERENCES bookings (id),
  close_activity_id    text UNIQUE,
  started_at           timestamptz,
  duration_s           integer CHECK (duration_s >= 0),
  recording_path       text CHECK (recording_path !~ '^/' AND recording_path !~ '\.\.'),
  transcript_path      text CHECK (transcript_path !~ '^/' AND transcript_path !~ '\.\.'),
  recording_sha256     text CHECK (recording_sha256 ~ '^[0-9a-f]{64}$'),
  transcription_model  text,
  diarization_model    text,
  readiness_rating     smallint CHECK (readiness_rating BETWEEN 1 AND 5),
  processing_status    text NOT NULL DEFAULT 'pending'
                         CHECK (processing_status IN ('pending', 'transcribed', 'extracted', 'reviewed', 'failed')),
  reviewed_by          uuid REFERENCES actors (id),
  reviewed_at          timestamptz,
  purge_after          timestamptz,
  purged_at            timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX calls_lead ON calls (lead_id);
CREATE INDEX calls_purge ON calls (purge_after) WHERE purged_at IS NULL;
COMMENT ON COLUMN calls.recording_path IS 'Relative to PRIVATE_DATA_DIR on the workstation. Never content.';
COMMENT ON COLUMN calls.transcript_path IS 'Relative to PRIVATE_DATA_DIR on the workstation. Never content.';

CREATE TABLE call_extractions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  call_id               uuid NOT NULL REFERENCES calls (id),
  field_name            text NOT NULL CHECK (field_name ~ '^[a-z][a-z0-9_]*$'),
  value_json            jsonb,
  value_ref             text CHECK (value_ref !~ '^/' AND value_ref !~ '\.\.'),
  span_start_ms         integer CHECK (span_start_ms >= 0),
  span_end_ms           integer,
  speaker               text CHECK (speaker IN ('sam', 'prospect', 'other')),
  confidence            real CHECK (confidence BETWEEN 0 AND 1),
  prompt_version        text NOT NULL,
  model                 text NOT NULL,
  job_id                uuid REFERENCES jobs (id),
  corrected_value_json  jsonb,
  corrected_value_ref   text CHECK (corrected_value_ref !~ '^/' AND corrected_value_ref !~ '\.\.'),
  corrected_by          uuid REFERENCES actors (id),
  corrected_at          timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CHECK (span_end_ms IS NULL OR span_end_ms >= span_start_ms),
  CHECK (value_json IS NOT NULL OR value_ref IS NOT NULL)
);
CREATE INDEX call_extractions_call ON call_extractions (call_id, field_name);
COMMENT ON COLUMN call_extractions.value_ref IS
  'Pointer into the workstation''s local store for free text derived from a call. The text never enters the Ledger.';

CREATE TABLE safety_flags (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     uuid NOT NULL REFERENCES leads (id),
  source      text NOT NULL CHECK (source IN ('intake', 'transcript')),
  call_id     uuid REFERENCES calls (id),
  layer       text NOT NULL CHECK (layer IN ('phrase_list', 'model')),
  category    text NOT NULL CHECK (category IN ('crisis', 'self_harm', 'clinical', 'addiction', 'medical', 'other')),
  span_ref    jsonb,
  score       real,
  job_id      uuid REFERENCES jobs (id),
  cleared_by  uuid REFERENCES actors (id),
  cleared_at  timestamptz,
  resolution  text CHECK (resolution IN ('cleared_false_alarm', 'referred_out', 'other')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CHECK ((source = 'transcript') = (call_id IS NOT NULL)),
  CHECK ((cleared_by IS NULL) = (cleared_at IS NULL) AND (cleared_by IS NULL) = (resolution IS NULL))
);
CREATE INDEX safety_flags_open ON safety_flags (lead_id) WHERE cleared_at IS NULL;

-- ---------------------------------------------------------------------------
-- Coaches and vetting
-- ---------------------------------------------------------------------------
CREATE TABLE coaches (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name               text NOT NULL,
  legal_name                 text,
  email                      text,
  phone                      text,
  website                    text,
  profile_summary            text,
  price_per_month_cents      integer CHECK (price_per_month_cents >= 0),
  program_length_months      smallint CHECK (program_length_months > 0),
  capacity_open              smallint CHECK (capacity_open >= 0),
  capacity_confirmed_at      timestamptz,
  home_area                  text,
  serves_remote              boolean NOT NULL DEFAULT false,
  status                     text NOT NULL DEFAULT 'prospect'
                               CHECK (status IN ('prospect', 'vetting', 'active', 'paused', 'removed')),
  status_changed_by          uuid REFERENCES actors (id),
  status_changed_at          timestamptz,
  partner_agreement_version  text,
  source                     text CHECK (source IN ('research', 'referral', 'inbound')),
  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE coach_need_states (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  coach_id        uuid NOT NULL REFERENCES coaches (id),
  need_state_key  text NOT NULL REFERENCES need_states (key),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (coach_id, need_state_key)
);

CREATE TABLE vetting_items (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  coach_id           uuid NOT NULL REFERENCES coaches (id),
  item_type          text NOT NULL CHECK (item_type IN ('interview', 'reference_1', 'reference_2',
                       'paying_client_evidence', 'program_and_price', 'signed_agreement')),
  evidence_location  text,
  notes              text,
  verified_by        uuid REFERENCES actors (id),
  verified_at        timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (coach_id, item_type),
  CHECK ((verified_by IS NULL) = (verified_at IS NULL))
);

CREATE FUNCTION vetting_items_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM ledger_require_founder(NEW.verified_by, 'vetting_items.verified_by');
  IF NEW.verified_by IS NOT NULL AND NEW.verified_by <> ledger_current_actor_id() THEN
    RAISE EXCEPTION 'a vetting item is verified by the founder making the change'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER vetting_items_founder BEFORE INSERT OR UPDATE OF verified_by ON vetting_items
  FOR EACH ROW EXECUTE FUNCTION vetting_items_guard();

-- A coach becomes active only by a founder's hand, and only with every vetting
-- item verified by a founder. Every other status change is stamped too.
CREATE FUNCTION coaches_status_guard() RETURNS trigger
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
CREATE TRIGGER coaches_status BEFORE INSERT OR UPDATE ON coaches
  FOR EACH ROW EXECUTE FUNCTION coaches_status_guard();

-- ---------------------------------------------------------------------------
-- Matching
-- ---------------------------------------------------------------------------
CREATE TABLE matches (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id                 uuid NOT NULL REFERENCES leads (id),
  run                     text NOT NULL CHECK (run IN ('pre_call', 'post_call')),
  status                  text NOT NULL DEFAULT 'proposed'
                            CHECK (status IN ('proposed', 'confirmed', 'introduced', 'accepted', 'declined', 'expired')),
  chosen_coach_id         uuid REFERENCES coaches (id),
  confirmed_by            uuid REFERENCES actors (id),
  confirmed_at            timestamptz,
  override_reason         text,
  prompt_version          text,
  model                   text,
  job_id                  uuid REFERENCES jobs (id),
  introduced_at           timestamptz,
  coach_response          text CHECK (coach_response IN ('accepted', 'declined', 'no_response')),
  coach_responded_at      timestamptz,
  coach_response_seconds  integer GENERATED ALWAYS AS
                            (extract(epoch FROM (coach_responded_at - introduced_at))::integer) STORED,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  CHECK ((confirmed_by IS NULL) = (confirmed_at IS NULL)),
  CHECK (status = 'proposed' OR (confirmed_by IS NOT NULL AND chosen_coach_id IS NOT NULL))
);
CREATE INDEX matches_lead ON matches (lead_id);

CREATE TABLE match_candidates (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id         uuid NOT NULL REFERENCES matches (id),
  coach_id         uuid NOT NULL REFERENCES coaches (id),
  passed_filters   boolean NOT NULL,
  filter_failures  jsonb NOT NULL DEFAULT '[]'::jsonb,
  rank             smallint CHECK (rank >= 1),
  reasons          jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (match_id, coach_id),
  UNIQUE (match_id, rank),
  CHECK (passed_filters OR rank IS NULL)
);

CREATE FUNCTION matches_confirm_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_top uuid;
BEGIN
  PERFORM ledger_require_founder(NEW.confirmed_by, 'matches.confirmed_by');
  IF NEW.confirmed_by IS NOT NULL AND NEW.chosen_coach_id IS NOT NULL THEN
    SELECT coach_id INTO v_top FROM match_candidates WHERE match_id = NEW.id AND rank = 1;
    IF NEW.chosen_coach_id IS DISTINCT FROM v_top AND coalesce(btrim(NEW.override_reason), '') = '' THEN
      RAISE EXCEPTION 'choosing a coach other than the top-ranked one needs an override_reason'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER matches_confirm BEFORE INSERT OR UPDATE ON matches
  FOR EACH ROW EXECUTE FUNCTION matches_confirm_guard();

-- ---------------------------------------------------------------------------
-- Fit calls, outcomes, money
-- ---------------------------------------------------------------------------
CREATE TABLE fit_calls (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id          uuid NOT NULL REFERENCES matches (id),
  lead_id           uuid NOT NULL REFERENCES leads (id),
  coach_id          uuid NOT NULL REFERENCES coaches (id),
  scheduled_for     timestamptz,
  booking_ref       text,
  held              boolean,
  held_reported_by  uuid REFERENCES actors (id),
  held_reported_at  timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outcomes (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fit_call_id          uuid NOT NULL REFERENCES fit_calls (id),
  lead_id              uuid NOT NULL REFERENCES leads (id),
  coach_id             uuid NOT NULL REFERENCES coaches (id),
  result               text NOT NULL DEFAULT 'pending' CHECK (result IN ('closed', 'lost', 'pending')),
  reported_by          uuid REFERENCES actors (id),
  reported_at          timestamptz,
  closed_at            timestamptz,
  first_payment_at     timestamptz,
  program_price_cents  integer CHECK (program_price_cents >= 0),
  lost_reason          text CHECK (lost_reason ~ '^[a-z][a-z0-9_]*$'),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE commissions (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  outcome_id              uuid NOT NULL UNIQUE REFERENCES outcomes (id),
  coach_id                uuid NOT NULL REFERENCES coaches (id),
  amount_cents            integer NOT NULL CHECK (amount_cents >= 0),
  earned_at               timestamptz,
  attribution_window_end  timestamptz,
  invoice_draft_ref       text,
  approval_id             uuid REFERENCES approvals (id),
  qbo_invoice_id          text,
  invoiced_at             timestamptz,
  due_at                  timestamptz,
  paid_at                 timestamptz,
  status                  text NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'approved', 'sent', 'paid', 'reversed', 'written_off')),
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  CHECK (status = 'draft' OR approval_id IS NOT NULL)
);

CREATE TABLE commission_adjustments (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  commission_id  uuid NOT NULL REFERENCES commissions (id),
  kind           text NOT NULL CHECK (kind IN ('refund_reversal', 'partial_reversal', 'chargeback', 'write_off')),
  amount_cents   integer NOT NULL CHECK (amount_cents < 0),
  reason         text,
  approval_id    uuid NOT NULL REFERENCES approvals (id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Approvals that gate money or messages are decided by founders.
CREATE FUNCTION approvals_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM ledger_require_founder(NEW.decided_by, 'approvals.decided_by');
  IF NEW.decided_by IS NOT NULL AND NEW.decided_by <> ledger_current_actor_id() THEN
    RAISE EXCEPTION 'an approval is decided by the founder making the change' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.decision IS NOT NULL AND
     (NEW.decision IS DISTINCT FROM OLD.decision OR NEW.proposal_hash IS DISTINCT FROM OLD.proposal_hash) THEN
    RAISE EXCEPTION 'a decided approval cannot change; create a new one' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER approvals_founder BEFORE INSERT OR UPDATE ON approvals
  FOR EACH ROW EXECUTE FUNCTION approvals_guard();

-- ---------------------------------------------------------------------------
-- Messaging
-- ---------------------------------------------------------------------------
CREATE TABLE templates (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key          text NOT NULL CHECK (key ~ '^[a-z][a-z0-9-]*$'),
  version      integer NOT NULL CHECK (version >= 1),
  channel      text NOT NULL CHECK (channel IN ('sms', 'email', 'slack')),
  audience     text NOT NULL CHECK (audience IN ('prospect', 'coach')),
  body         text NOT NULL,
  variables    jsonb NOT NULL DEFAULT '[]'::jsonb,
  status       text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'retired')),
  approved_by  uuid REFERENCES actors (id),
  approved_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (key, version),
  CHECK ((status = 'draft') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);

CREATE FUNCTION templates_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM ledger_require_founder(NEW.approved_by, 'templates.approved_by');
  IF TG_OP = 'UPDATE' AND OLD.status <> 'draft' AND
     (NEW.body IS DISTINCT FROM OLD.body OR NEW.key IS DISTINCT FROM OLD.key OR
      NEW.version IS DISTINCT FROM OLD.version OR NEW.channel IS DISTINCT FROM OLD.channel OR
      NEW.audience IS DISTINCT FROM OLD.audience OR NEW.variables IS DISTINCT FROM OLD.variables) THEN
    RAISE EXCEPTION 'template %.v% is approved and immutable; add a new version', OLD.key, OLD.version
      USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'retired' AND NEW.status <> 'retired' THEN
    RAISE EXCEPTION 'a retired template stays retired' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER templates_immutable BEFORE INSERT OR UPDATE ON templates
  FOR EACH ROW EXECUTE FUNCTION templates_guard();

CREATE TABLE outbound_messages (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recipient_type       text NOT NULL CHECK (recipient_type IN ('lead', 'coach')),
  lead_id              uuid REFERENCES leads (id),
  coach_id             uuid REFERENCES coaches (id),
  channel              text NOT NULL CHECK (channel IN ('sms', 'email')),
  template_id          uuid REFERENCES templates (id),
  approval_id          uuid REFERENCES approvals (id),
  rendered_hash        text,
  provider_message_id  text,
  status               text NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued', 'sent', 'delivered', 'failed', 'refused')),
  refusal_reason       text CHECK (refusal_reason IN ('no_consent', 'safety_hold', 'unapproved', 'other')),
  sent_at              timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CHECK ((recipient_type = 'lead' AND lead_id IS NOT NULL AND coach_id IS NULL) OR
         (recipient_type = 'coach' AND coach_id IS NOT NULL AND lead_id IS NULL)),
  CHECK ((template_id IS NULL) <> (approval_id IS NULL)),
  CHECK ((status = 'refused') = (refusal_reason IS NOT NULL))
);
CREATE INDEX outbound_messages_lead ON outbound_messages (lead_id) WHERE lead_id IS NOT NULL;

-- Defence in depth behind the n8n send-gate: the Ledger itself refuses to
-- record a message as going out when the gate's conditions do not hold.
CREATE FUNCTION outbound_messages_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_lead      leads%ROWTYPE;
  v_template  templates%ROWTYPE;
  v_approval  approvals%ROWTYPE;
BEGIN
  IF NEW.status = 'refused' OR NEW.status = 'failed' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('sent', 'delivered') THEN
    RETURN NEW;  -- delivery receipts for a message already gated
  END IF;
  IF NEW.template_id IS NOT NULL THEN
    SELECT * INTO v_template FROM templates WHERE id = NEW.template_id;
    IF v_template.status <> 'approved' THEN
      RAISE EXCEPTION 'template is not approved' USING ERRCODE = 'check_violation';
    END IF;
    IF v_template.channel <> NEW.channel THEN
      RAISE EXCEPTION 'template channel does not match message channel' USING ERRCODE = 'check_violation';
    END IF;
  ELSE
    SELECT * INTO v_approval FROM approvals WHERE id = NEW.approval_id;
    IF v_approval.decision IS DISTINCT FROM 'approved' AND v_approval.decision IS DISTINCT FROM 'edited' THEN
      RAISE EXCEPTION 'approval is not approved' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  IF NEW.lead_id IS NOT NULL THEN
    SELECT * INTO v_lead FROM leads WHERE id = NEW.lead_id;
    IF v_lead.safety_hold THEN
      RAISE EXCEPTION 'lead is on a safety hold' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.channel = 'sms' AND NOT v_lead.consent_text THEN
      RAISE EXCEPTION 'lead has not consented to texts' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER outbound_messages_send_gate BEFORE INSERT OR UPDATE ON outbound_messages
  FOR EACH ROW EXECUTE FUNCTION outbound_messages_gate();

-- ---------------------------------------------------------------------------
-- Attach updated_at and event triggers everywhere
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  -- table => primary key column, then sensitive columns
  spec jsonb := '{
    "actors":                 ["id"],
    "need_states":            ["key"],
    "jobs":                   ["id", "payload", "result_ref", "error_detail"],
    "approvals":              ["id", "decision_note"],
    "ads":                    ["id"],
    "ad_daily":               ["id"],
    "leads":                  ["id", "first_name", "phone_e164", "email", "intake_note"],
    "consent_records":        ["id", "evidence_ref"],
    "bookings":               ["id"],
    "calls":                  ["id"],
    "call_extractions":       ["id", "value_json", "value_ref", "corrected_value_json", "corrected_value_ref"],
    "safety_flags":           ["id", "span_ref"],
    "coaches":                ["id", "legal_name", "email", "phone"],
    "coach_need_states":      ["id"],
    "vetting_items":          ["id", "notes", "evidence_location"],
    "matches":                ["id", "override_reason"],
    "match_candidates":       ["id", "reasons"],
    "fit_calls":              ["id"],
    "outcomes":               ["id"],
    "commissions":            ["id"],
    "commission_adjustments": ["id", "reason"],
    "templates":              ["id"],
    "outbound_messages":      ["id"]
  }';
  t text;
  args text;
BEGIN
  FOR t IN SELECT jsonb_object_keys(spec) LOOP
    SELECT string_agg(quote_literal(v), ', ') INTO args FROM jsonb_array_elements_text(spec -> t) AS v;
    EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I
                    FOR EACH ROW EXECUTE FUNCTION ledger_log_change(%s)', t || '_events', t, args);
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = t AND column_name = 'updated_at') THEN
      EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON %I
                      FOR EACH ROW EXECUTE FUNCTION ledger_touch_updated_at()', t || '_updated_at', t);
    END IF;
  END LOOP;
END $$;

CREATE TRIGGER worker_heartbeats_updated_at BEFORE UPDATE ON worker_heartbeats
  FOR EACH ROW EXECUTE FUNCTION ledger_touch_updated_at();

-- ---------------------------------------------------------------------------
-- api schema: the only thing n8n can touch
-- ---------------------------------------------------------------------------
CREATE SCHEMA api;

CREATE FUNCTION api.enqueue_job(
  p_job_type        text,
  p_payload         jsonb DEFAULT '{}'::jsonb,
  p_entity_type     text DEFAULT NULL,
  p_entity_id       uuid DEFAULT NULL,
  p_idempotency_key text DEFAULT NULL,
  p_priority        smallint DEFAULT 100
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_id uuid;
BEGIN
  PERFORM set_config('app.actor', 'n8n', true);
  INSERT INTO jobs (job_type, payload, entity_type, entity_id, idempotency_key, priority)
  VALUES (p_job_type, coalesce(p_payload, '{}'::jsonb), p_entity_type, p_entity_id,
          p_idempotency_key, coalesce(p_priority, 100))
  ON CONFLICT (idempotency_key) DO NOTHING
  RETURNING id INTO v_id;
  IF v_id IS NULL THEN
    SELECT id INTO v_id FROM jobs WHERE idempotency_key = p_idempotency_key;
  END IF;
  RETURN v_id;
END $$;

-- What the stale-job alert reads. IDs and numbers only.
-- should_alert is true on the check that first finds the queue stale, then once
-- every p_repeat_minutes while it stays stale (so a workstation that is off
-- overnight produces a few alerts, not one every check).
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

-- Error branch of every n8n workflow: an event with IDs only, never payload data.
CREATE FUNCTION api.record_workflow_error(
  p_workflow      text,
  p_execution_id  text,
  p_node          text DEFAULT NULL,
  p_error_class   text DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  PERFORM set_config('app.actor', 'n8n', true);
  RETURN ledger_emit('n8n_workflow', left(p_workflow, 100), 'workflow.failed',
                     jsonb_build_object('execution_id', left(p_execution_id, 64),
                                        'node', left(p_node, 100),
                                        'error_class', left(p_error_class, 100)));
END $$;

REVOKE ALL ON SCHEMA api FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA api FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Privileges
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA api TO ledger_n8n;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA api TO ledger_n8n;

REVOKE ALL ON SCHEMA public FROM PUBLIC;  -- n8n (and any other role) cannot even see tables
GRANT USAGE ON SCHEMA public TO ledger_worker, ledger_console, ledger_reporting;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ledger_worker, ledger_console, ledger_reporting;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO ledger_worker, ledger_console;
GRANT DELETE ON worker_heartbeats TO ledger_worker;
REVOKE INSERT, UPDATE ON actors FROM ledger_worker, ledger_console;
REVOKE UPDATE, DELETE, TRUNCATE ON events, consent_records FROM ledger_worker, ledger_console, ledger_reporting;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO ledger_worker, ledger_console;

-- migrate:down

DROP SCHEMA api CASCADE;
DROP TABLE IF EXISTS
  outbound_messages, templates, commission_adjustments, commissions, outcomes, fit_calls,
  match_candidates, matches, vetting_items, coach_need_states, coaches, safety_flags,
  call_extractions, calls, bookings, consent_records, leads, ad_daily, ads,
  worker_heartbeats, events, approvals, jobs, need_states, actors
  CASCADE;
DROP FUNCTION IF EXISTS
  ledger_touch_updated_at(), ledger_forbid_change(), ledger_current_actor_id(),
  ledger_is_founder(uuid), ledger_require_founder(uuid, text), ledger_log_change(),
  ledger_emit(text, text, text, jsonb, jsonb, jsonb), leads_safety_hold_guard(),
  vetting_items_guard(), coaches_status_guard(), matches_confirm_guard(), approvals_guard(),
  templates_guard(), outbound_messages_gate();
-- Group roles are cluster-wide and may own grants in other databases; they are
-- left in place on purpose.
