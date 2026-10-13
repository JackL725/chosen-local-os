\restrict dbmate

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: api; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA api;


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: enqueue_job(text, jsonb, text, uuid, text, smallint); Type: FUNCTION; Schema: api; Owner: -
--

CREATE FUNCTION api.enqueue_job(p_job_type text, p_payload jsonb DEFAULT '{}'::jsonb, p_entity_type text DEFAULT NULL::text, p_entity_id uuid DEFAULT NULL::uuid, p_idempotency_key text DEFAULT NULL::text, p_priority smallint DEFAULT 100) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
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


--
-- Name: queue_health(integer); Type: FUNCTION; Schema: api; Owner: -
--

CREATE FUNCTION api.queue_health(p_check_every_min integer DEFAULT 15) RETURNS TABLE(oldest_pending_job_id uuid, oldest_pending_job_type text, oldest_pending_age_min integer, pending_count integer, paused_pending_count integer, failed_last_24h integer, newest_heartbeat_age_min integer, threshold_hours numeric, is_stale boolean, should_alert boolean)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
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


--
-- Name: record_workflow_error(text, text, text, text); Type: FUNCTION; Schema: api; Owner: -
--

CREATE FUNCTION api.record_workflow_error(p_workflow text, p_execution_id text, p_node text DEFAULT NULL::text, p_error_class text DEFAULT NULL::text) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
  PERFORM set_config('app.actor', 'n8n', true);
  RETURN ledger_emit('n8n_workflow', left(p_workflow, 100), 'workflow.failed',
                     jsonb_build_object('execution_id', left(p_execution_id, 64),
                                        'node', left(p_node, 100),
                                        'error_class', left(p_error_class, 100)));
END $$;


--
-- Name: approvals_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.approvals_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: coaches_status_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.coaches_status_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: console_users_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.console_users_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM actors WHERE id = NEW.actor_id AND kind = 'founder') THEN
    RAISE EXCEPTION 'console users are founders' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;


--
-- Name: leads_safety_hold_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.leads_safety_hold_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: ledger_current_actor_id(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_current_actor_id() RETURNS uuid
    LANGUAGE plpgsql STABLE
    AS $$
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


--
-- Name: ledger_default_market(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_default_market() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: ledger_emit(text, text, text, jsonb, jsonb, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_emit(p_entity_type text, p_entity_id text, p_event_type text, p_payload jsonb DEFAULT '{}'::jsonb, p_input_ref jsonb DEFAULT NULL::jsonb, p_output_ref jsonb DEFAULT NULL::jsonb) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: ledger_forbid_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_forbid_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;


--
-- Name: ledger_is_founder(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_is_founder(p_actor uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
  SELECT EXISTS (SELECT 1 FROM actors WHERE id = p_actor AND kind = 'founder' AND active)
$$;


--
-- Name: ledger_is_rule_writer(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_is_rule_writer(p_actor uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
  SELECT EXISTS (SELECT 1 FROM actors WHERE id = p_actor AND active
                   AND (kind = 'founder' OR name = 'migration'))
$$;


--
-- Name: ledger_log_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_log_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: ledger_login_group(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_login_group() RETURNS text
    LANGUAGE plpgsql STABLE
    AS $$
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


--
-- Name: ledger_require_founder(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_require_founder(p_actor uuid, p_what text) RETURNS void
    LANGUAGE plpgsql STABLE
    AS $$
BEGIN
  IF p_actor IS NOT NULL AND NOT ledger_is_founder(p_actor) THEN
    RAISE EXCEPTION '% must be a founder', p_what USING ERRCODE = 'check_violation';
  END IF;
END $$;


--
-- Name: ledger_rule_writer_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_rule_writer_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NOT ledger_is_rule_writer(ledger_current_actor_id()) THEN
    RAISE EXCEPTION 'only a founder can change %', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END $$;


--
-- Name: ledger_setting(text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_setting(p_key text, p_default jsonb) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
  SELECT coalesce((SELECT value FROM settings WHERE key = p_key), p_default)
$$;


--
-- Name: ledger_touch_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ledger_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;


--
-- Name: market_need_states_stamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.market_need_states_stamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.launched IS DISTINCT FROM OLD.launched THEN
    NEW.changed_by := ledger_current_actor_id();
    NEW.changed_at := now();
  END IF;
  RETURN NEW;
END $$;


--
-- Name: markets_time_zone_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.markets_time_zone_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = NEW.time_zone) THEN
    RAISE EXCEPTION 'unknown time zone %', NEW.time_zone USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;


--
-- Name: matches_confirm_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.matches_confirm_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: outbound_messages_gate(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.outbound_messages_gate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: settings_stamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.settings_stamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.changed_by := ledger_current_actor_id();
  RETURN NEW;
END $$;


--
-- Name: systems_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.systems_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: templates_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.templates_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: vetting_evidence_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.vetting_evidence_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: vetting_item_types_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.vetting_item_types_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: vetting_items_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.vetting_items_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  PERFORM ledger_require_founder(NEW.verified_by, 'vetting_items.verified_by');
  IF NEW.verified_by IS NOT NULL AND NEW.verified_by <> ledger_current_actor_id() THEN
    RAISE EXCEPTION 'a vetting item is verified by the founder making the change'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;


--
-- Name: vetting_items_type_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.vetting_items_type_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: vetting_standards_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.vetting_standards_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: actors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.actors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    name text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT actors_kind_check CHECK ((kind = ANY (ARRAY['founder'::text, 'system'::text, 'coach'::text, 'lead'::text, 'external'::text])))
);


--
-- Name: ad_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ad_daily (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ad_id uuid NOT NULL,
    date date NOT NULL,
    spend_cents integer DEFAULT 0 NOT NULL,
    impressions integer DEFAULT 0 NOT NULL,
    clicks integer DEFAULT 0 NOT NULL,
    platform_leads integer DEFAULT 0 NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    import_run_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ad_daily_clicks_check CHECK ((clicks >= 0)),
    CONSTRAINT ad_daily_impressions_check CHECK ((impressions >= 0)),
    CONSTRAINT ad_daily_platform_leads_check CHECK ((platform_leads >= 0)),
    CONSTRAINT ad_daily_spend_cents_check CHECK ((spend_cents >= 0))
);


--
-- Name: ads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    platform text NOT NULL,
    platform_ad_id text NOT NULL,
    campaign_id text,
    adset_id text,
    name text,
    need_state_key text,
    status text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    market_key text NOT NULL,
    CONSTRAINT ads_platform_check CHECK ((platform = ANY (ARRAY['meta'::text, 'tiktok'::text])))
);


--
-- Name: approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approvals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    subject_type text NOT NULL,
    subject_id uuid NOT NULL,
    proposed_by uuid NOT NULL,
    proposal_ref text,
    proposal_hash text,
    decided_by uuid,
    decision text,
    decision_note text,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT approvals_check CHECK ((((decision IS NULL) = (decided_by IS NULL)) AND ((decision IS NULL) = (decided_at IS NULL)))),
    CONSTRAINT approvals_decision_check CHECK ((decision = ANY (ARRAY['approved'::text, 'rejected'::text, 'edited'::text]))),
    CONSTRAINT approvals_kind_check CHECK ((kind = ANY (ARRAY['message'::text, 'outreach'::text, 'invoice'::text, 'match'::text, 'template'::text, 'spend'::text, 'safety_clear'::text])))
);


--
-- Name: bookings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bookings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lead_id uuid NOT NULL,
    calcom_booking_uid text,
    scheduled_for timestamp with time zone NOT NULL,
    status text DEFAULT 'booked'::text NOT NULL,
    status_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bookings_status_check CHECK ((status = ANY (ARRAY['booked'::text, 'rescheduled'::text, 'cancelled'::text, 'attended'::text, 'no_show'::text])))
);


--
-- Name: call_extractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_extractions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    call_id uuid NOT NULL,
    field_name text NOT NULL,
    value_json jsonb,
    value_ref text,
    span_start_ms integer,
    span_end_ms integer,
    speaker text,
    confidence real,
    prompt_version text NOT NULL,
    model text NOT NULL,
    job_id uuid,
    corrected_value_json jsonb,
    corrected_value_ref text,
    corrected_by uuid,
    corrected_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT call_extractions_check CHECK (((span_end_ms IS NULL) OR (span_end_ms >= span_start_ms))),
    CONSTRAINT call_extractions_check1 CHECK (((value_json IS NOT NULL) OR (value_ref IS NOT NULL))),
    CONSTRAINT call_extractions_confidence_check CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision))),
    CONSTRAINT call_extractions_corrected_value_ref_check CHECK (((corrected_value_ref !~ '^/'::text) AND (corrected_value_ref !~ '\.\.'::text))),
    CONSTRAINT call_extractions_field_name_check CHECK ((field_name ~ '^[a-z][a-z0-9_]*$'::text)),
    CONSTRAINT call_extractions_span_start_ms_check CHECK ((span_start_ms >= 0)),
    CONSTRAINT call_extractions_speaker_check CHECK ((speaker = ANY (ARRAY['sam'::text, 'prospect'::text, 'other'::text]))),
    CONSTRAINT call_extractions_value_ref_check CHECK (((value_ref !~ '^/'::text) AND (value_ref !~ '\.\.'::text)))
);


--
-- Name: COLUMN call_extractions.value_ref; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.call_extractions.value_ref IS 'Pointer into the workstation''s local store for free text derived from a call. The text never enters the Ledger.';


--
-- Name: calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.calls (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lead_id uuid NOT NULL,
    booking_id uuid,
    close_activity_id text,
    started_at timestamp with time zone,
    duration_s integer,
    recording_path text,
    transcript_path text,
    recording_sha256 text,
    transcription_model text,
    diarization_model text,
    readiness_rating smallint,
    processing_status text DEFAULT 'pending'::text NOT NULL,
    reviewed_by uuid,
    reviewed_at timestamp with time zone,
    purge_after timestamp with time zone,
    purged_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT calls_duration_s_check CHECK ((duration_s >= 0)),
    CONSTRAINT calls_processing_status_check CHECK ((processing_status = ANY (ARRAY['pending'::text, 'transcribed'::text, 'extracted'::text, 'reviewed'::text, 'failed'::text]))),
    CONSTRAINT calls_readiness_rating_check CHECK (((readiness_rating >= 1) AND (readiness_rating <= 5))),
    CONSTRAINT calls_recording_path_check CHECK (((recording_path !~ '^/'::text) AND (recording_path !~ '\.\.'::text))),
    CONSTRAINT calls_recording_sha256_check CHECK ((recording_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT calls_transcript_path_check CHECK (((transcript_path !~ '^/'::text) AND (transcript_path !~ '\.\.'::text)))
);


--
-- Name: COLUMN calls.recording_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.calls.recording_path IS 'Relative to PRIVATE_DATA_DIR on the workstation. Never content.';


--
-- Name: COLUMN calls.transcript_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.calls.transcript_path IS 'Relative to PRIVATE_DATA_DIR on the workstation. Never content.';


--
-- Name: coach_need_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.coach_need_states (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    coach_id uuid NOT NULL,
    need_state_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: coaches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.coaches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    display_name text NOT NULL,
    legal_name text,
    email text,
    phone text,
    website text,
    profile_summary text,
    price_per_month_cents integer,
    program_length_months smallint,
    capacity_open smallint,
    capacity_confirmed_at timestamp with time zone,
    home_area text,
    serves_remote boolean DEFAULT false NOT NULL,
    status text DEFAULT 'prospect'::text NOT NULL,
    status_changed_by uuid,
    status_changed_at timestamp with time zone,
    partner_agreement_version text,
    source text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    market_key text NOT NULL,
    vetting_standard_id uuid,
    postal_code text,
    notes text,
    CONSTRAINT coaches_capacity_open_check CHECK ((capacity_open >= 0)),
    CONSTRAINT coaches_postal_code_check CHECK ((postal_code ~ '^[A-Za-z0-9 -]{3,10}$'::text)),
    CONSTRAINT coaches_price_per_month_cents_check CHECK ((price_per_month_cents >= 0)),
    CONSTRAINT coaches_program_length_months_check CHECK ((program_length_months > 0)),
    CONSTRAINT coaches_source_check CHECK ((source = ANY (ARRAY['research'::text, 'referral'::text, 'inbound'::text]))),
    CONSTRAINT coaches_status_check CHECK ((status = ANY (ARRAY['prospect'::text, 'vetting'::text, 'active'::text, 'paused'::text, 'removed'::text])))
);


--
-- Name: commission_adjustments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commission_adjustments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    commission_id uuid NOT NULL,
    kind text NOT NULL,
    amount_cents integer NOT NULL,
    reason text,
    approval_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT commission_adjustments_amount_cents_check CHECK ((amount_cents < 0)),
    CONSTRAINT commission_adjustments_kind_check CHECK ((kind = ANY (ARRAY['refund_reversal'::text, 'partial_reversal'::text, 'chargeback'::text, 'write_off'::text])))
);


--
-- Name: commissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commissions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    outcome_id uuid NOT NULL,
    coach_id uuid NOT NULL,
    amount_cents integer NOT NULL,
    earned_at timestamp with time zone,
    attribution_window_end timestamp with time zone,
    invoice_draft_ref text,
    approval_id uuid,
    qbo_invoice_id text,
    invoiced_at timestamp with time zone,
    due_at timestamp with time zone,
    paid_at timestamp with time zone,
    status text DEFAULT 'draft'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT commissions_amount_cents_check CHECK ((amount_cents >= 0)),
    CONSTRAINT commissions_check CHECK (((status = 'draft'::text) OR (approval_id IS NOT NULL))),
    CONSTRAINT commissions_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'approved'::text, 'sent'::text, 'paid'::text, 'reversed'::text, 'written_off'::text])))
);


--
-- Name: consent_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.consent_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lead_id uuid NOT NULL,
    channel text NOT NULL,
    action text NOT NULL,
    source text NOT NULL,
    wording_version text,
    evidence_ref text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT consent_records_action_check CHECK ((action = ANY (ARRAY['granted'::text, 'revoked'::text]))),
    CONSTRAINT consent_records_channel_check CHECK ((channel = ANY (ARRAY['call'::text, 'text'::text, 'email'::text, 'recording'::text, 'coach_share'::text]))),
    CONSTRAINT consent_records_source_check CHECK ((source = ANY (ARRAY['intake_form'::text, 'sms_reply'::text, 'call'::text, 'founder'::text])))
);


--
-- Name: console_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.console_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    console_user_id uuid NOT NULL,
    token_sha256 text NOT NULL,
    local boolean DEFAULT false NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT console_sessions_token_sha256_check CHECK ((token_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: console_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.console_users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    actor_id uuid NOT NULL,
    tailscale_login text,
    password_hash text NOT NULL,
    allow_local_signin boolean DEFAULT false NOT NULL,
    failed_signins smallint DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    password_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT console_users_failed_signins_check CHECK ((failed_signins >= 0))
);


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    entity_type text NOT NULL,
    entity_id text NOT NULL,
    event_type text NOT NULL,
    actor_id uuid NOT NULL,
    job_id uuid,
    approval_id uuid,
    prompt_version text,
    model text,
    input_ref jsonb,
    output_ref jsonb,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: fit_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fit_calls (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    match_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    coach_id uuid NOT NULL,
    scheduled_for timestamp with time zone,
    booking_ref text,
    held boolean,
    held_reported_by uuid,
    held_reported_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_type text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    entity_type text,
    entity_id uuid,
    status text DEFAULT 'pending'::text NOT NULL,
    priority smallint DEFAULT 100 NOT NULL,
    run_after timestamp with time zone DEFAULT now() NOT NULL,
    attempts smallint DEFAULT 0 NOT NULL,
    max_attempts smallint DEFAULT 3 NOT NULL,
    claimed_by text,
    claimed_at timestamp with time zone,
    lease_expires_at timestamp with time zone,
    idempotency_key text,
    result_ref jsonb,
    error_class text,
    error_detail text,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT jobs_error_detail_check CHECK ((length(error_detail) <= 500)),
    CONSTRAINT jobs_job_type_check CHECK ((job_type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$'::text)),
    CONSTRAINT jobs_max_attempts_check CHECK (((max_attempts >= 1) AND (max_attempts <= 10))),
    CONSTRAINT jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: leads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.leads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    first_name text,
    phone_e164 text,
    email text,
    need_state_key text,
    budget_band text,
    income_band text,
    start_timeframe text,
    intake_note text,
    submitted_at timestamp with time zone,
    source_platform text,
    ad_id uuid,
    utm_source text,
    utm_medium text,
    utm_campaign text,
    utm_content text,
    click_id text,
    landing_variant text,
    close_lead_id text,
    dedupe_key text,
    merged_into_lead_id uuid,
    status text DEFAULT 'new'::text NOT NULL,
    qualification_result text,
    qualification_reason text,
    prescore smallint,
    first_contact_at timestamp with time zone,
    consent_call boolean DEFAULT false NOT NULL,
    consent_text boolean DEFAULT false NOT NULL,
    consent_call_at timestamp with time zone,
    consent_text_at timestamp with time zone,
    consent_wording_version text,
    safety_hold boolean DEFAULT false NOT NULL,
    safety_hold_set_by uuid,
    safety_hold_set_at timestamp with time zone,
    safety_hold_cleared_by uuid,
    safety_hold_cleared_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    market_key text NOT NULL,
    CONSTRAINT leads_phone_e164_check CHECK ((phone_e164 ~ '^\+[1-9][0-9]{6,14}$'::text)),
    CONSTRAINT leads_qualification_reason_check CHECK ((qualification_reason ~ '^[a-z][a-z0-9_]*$'::text)),
    CONSTRAINT leads_qualification_result_check CHECK ((qualification_result = ANY (ARRAY['qualified'::text, 'not_qualified'::text]))),
    CONSTRAINT leads_source_platform_check CHECK ((source_platform = ANY (ARRAY['meta'::text, 'tiktok'::text, 'organic'::text, 'referral'::text, 'test'::text]))),
    CONSTRAINT leads_status_check CHECK ((status = ANY (ARRAY['new'::text, 'booked'::text, 'called'::text, 'qualified'::text, 'disqualified'::text, 'matched'::text, 'introduced'::text, 'won'::text, 'lost'::text, 'dormant'::text])))
);


--
-- Name: market_need_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_need_states (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    market_key text NOT NULL,
    need_state_key text NOT NULL,
    launched boolean DEFAULT true NOT NULL,
    changed_by uuid,
    changed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: markets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.markets (
    key text NOT NULL,
    name text NOT NULL,
    time_zone text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT markets_key_check CHECK ((key ~ '^[a-z][a-z0-9_]*$'::text))
);


--
-- Name: match_candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.match_candidates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    match_id uuid NOT NULL,
    coach_id uuid NOT NULL,
    passed_filters boolean NOT NULL,
    filter_failures jsonb DEFAULT '[]'::jsonb NOT NULL,
    rank smallint,
    reasons jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT match_candidates_check CHECK ((passed_filters OR (rank IS NULL))),
    CONSTRAINT match_candidates_rank_check CHECK ((rank >= 1))
);


--
-- Name: matches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.matches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lead_id uuid NOT NULL,
    run text NOT NULL,
    status text DEFAULT 'proposed'::text NOT NULL,
    chosen_coach_id uuid,
    confirmed_by uuid,
    confirmed_at timestamp with time zone,
    override_reason text,
    prompt_version text,
    model text,
    job_id uuid,
    introduced_at timestamp with time zone,
    coach_response text,
    coach_responded_at timestamp with time zone,
    coach_response_seconds integer GENERATED ALWAYS AS ((EXTRACT(epoch FROM (coach_responded_at - introduced_at)))::integer) STORED,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT matches_check CHECK (((confirmed_by IS NULL) = (confirmed_at IS NULL))),
    CONSTRAINT matches_check1 CHECK (((status = 'proposed'::text) OR ((confirmed_by IS NOT NULL) AND (chosen_coach_id IS NOT NULL)))),
    CONSTRAINT matches_coach_response_check CHECK ((coach_response = ANY (ARRAY['accepted'::text, 'declined'::text, 'no_response'::text]))),
    CONSTRAINT matches_run_check CHECK ((run = ANY (ARRAY['pre_call'::text, 'post_call'::text]))),
    CONSTRAINT matches_status_check CHECK ((status = ANY (ARRAY['proposed'::text, 'confirmed'::text, 'introduced'::text, 'accepted'::text, 'declined'::text, 'expired'::text])))
);


--
-- Name: need_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.need_states (
    key text NOT NULL,
    label text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    description text,
    covers text,
    not_covered text,
    sort_order smallint DEFAULT 100 NOT NULL,
    CONSTRAINT need_states_key_check CHECK ((key ~ '^[a-z][a-z0-9_]*$'::text))
);


--
-- Name: outbound_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbound_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    recipient_type text NOT NULL,
    lead_id uuid,
    coach_id uuid,
    channel text NOT NULL,
    template_id uuid,
    approval_id uuid,
    rendered_hash text,
    provider_message_id text,
    status text DEFAULT 'queued'::text NOT NULL,
    refusal_reason text,
    sent_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT outbound_messages_channel_check CHECK ((channel = ANY (ARRAY['sms'::text, 'email'::text]))),
    CONSTRAINT outbound_messages_check CHECK ((((recipient_type = 'lead'::text) AND (lead_id IS NOT NULL) AND (coach_id IS NULL)) OR ((recipient_type = 'coach'::text) AND (coach_id IS NOT NULL) AND (lead_id IS NULL)))),
    CONSTRAINT outbound_messages_check1 CHECK (((template_id IS NULL) <> (approval_id IS NULL))),
    CONSTRAINT outbound_messages_check2 CHECK (((status = 'refused'::text) = (refusal_reason IS NOT NULL))),
    CONSTRAINT outbound_messages_recipient_type_check CHECK ((recipient_type = ANY (ARRAY['lead'::text, 'coach'::text]))),
    CONSTRAINT outbound_messages_refusal_reason_check CHECK ((refusal_reason = ANY (ARRAY['no_consent'::text, 'safety_hold'::text, 'unapproved'::text, 'other'::text]))),
    CONSTRAINT outbound_messages_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'sent'::text, 'delivered'::text, 'failed'::text, 'refused'::text])))
);


--
-- Name: outcomes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outcomes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    fit_call_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    coach_id uuid NOT NULL,
    result text DEFAULT 'pending'::text NOT NULL,
    reported_by uuid,
    reported_at timestamp with time zone,
    closed_at timestamp with time zone,
    first_payment_at timestamp with time zone,
    program_price_cents integer,
    lost_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT outcomes_lost_reason_check CHECK ((lost_reason ~ '^[a-z][a-z0-9_]*$'::text)),
    CONSTRAINT outcomes_program_price_cents_check CHECK ((program_price_cents >= 0)),
    CONSTRAINT outcomes_result_check CHECK ((result = ANY (ARRAY['closed'::text, 'lost'::text, 'pending'::text])))
);


--
-- Name: safety_flags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.safety_flags (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lead_id uuid NOT NULL,
    source text NOT NULL,
    call_id uuid,
    layer text NOT NULL,
    category text NOT NULL,
    span_ref jsonb,
    score real,
    job_id uuid,
    cleared_by uuid,
    cleared_at timestamp with time zone,
    resolution text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT safety_flags_category_check CHECK ((category = ANY (ARRAY['crisis'::text, 'self_harm'::text, 'clinical'::text, 'addiction'::text, 'medical'::text, 'other'::text]))),
    CONSTRAINT safety_flags_check CHECK (((source = 'transcript'::text) = (call_id IS NOT NULL))),
    CONSTRAINT safety_flags_check1 CHECK ((((cleared_by IS NULL) = (cleared_at IS NULL)) AND ((cleared_by IS NULL) = (resolution IS NULL)))),
    CONSTRAINT safety_flags_layer_check CHECK ((layer = ANY (ARRAY['phrase_list'::text, 'model'::text]))),
    CONSTRAINT safety_flags_resolution_check CHECK ((resolution = ANY (ARRAY['cleared_false_alarm'::text, 'referred_out'::text, 'other'::text]))),
    CONSTRAINT safety_flags_source_check CHECK ((source = ANY (ARRAY['intake'::text, 'transcript'::text])))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settings (
    key text NOT NULL,
    value jsonb NOT NULL,
    changed_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT settings_key_check CHECK ((key ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'::text))
);


--
-- Name: system_job_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_job_types (
    job_type text NOT NULL,
    system_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT system_job_types_job_type_check CHECK ((job_type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$'::text))
);


--
-- Name: systems; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.systems (
    key text NOT NULL,
    title text NOT NULL,
    version integer NOT NULL,
    required boolean DEFAULT false NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    enabled_changed_by uuid,
    enabled_changed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT systems_check CHECK ((enabled OR (NOT required))),
    CONSTRAINT systems_key_check CHECK ((key ~ '^[a-z][a-z0-9_]{1,39}$'::text)),
    CONSTRAINT systems_version_check CHECK ((version >= 1))
);


--
-- Name: templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key text NOT NULL,
    version integer NOT NULL,
    channel text NOT NULL,
    audience text NOT NULL,
    body text NOT NULL,
    variables jsonb DEFAULT '[]'::jsonb NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    approved_by uuid,
    approved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT templates_audience_check CHECK ((audience = ANY (ARRAY['prospect'::text, 'coach'::text]))),
    CONSTRAINT templates_channel_check CHECK ((channel = ANY (ARRAY['sms'::text, 'email'::text, 'slack'::text]))),
    CONSTRAINT templates_check CHECK (((status = 'draft'::text) OR ((approved_by IS NOT NULL) AND (approved_at IS NOT NULL)))),
    CONSTRAINT templates_key_check CHECK ((key ~ '^[a-z][a-z0-9-]*$'::text)),
    CONSTRAINT templates_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'approved'::text, 'retired'::text]))),
    CONSTRAINT templates_version_check CHECK ((version >= 1))
);


--
-- Name: vetting_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vetting_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    vetting_item_id uuid NOT NULL,
    kind text NOT NULL,
    relative_path text,
    display_name text,
    content_type text,
    sha256 text,
    size_bytes bigint,
    note text,
    uploaded_by uuid NOT NULL,
    removed_by uuid,
    removed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vetting_evidence_check CHECK (((kind = 'file'::text) = ((relative_path IS NOT NULL) AND (sha256 IS NOT NULL) AND (size_bytes IS NOT NULL)))),
    CONSTRAINT vetting_evidence_check1 CHECK (((kind = 'note'::text) = (note IS NOT NULL))),
    CONSTRAINT vetting_evidence_check2 CHECK (((removed_by IS NULL) = (removed_at IS NULL))),
    CONSTRAINT vetting_evidence_kind_check CHECK ((kind = ANY (ARRAY['file'::text, 'note'::text]))),
    CONSTRAINT vetting_evidence_relative_path_check CHECK (((relative_path !~ '^/'::text) AND (relative_path !~ '(^|/)\.\.(/|$)'::text))),
    CONSTRAINT vetting_evidence_sha256_check CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT vetting_evidence_size_bytes_check CHECK ((size_bytes >= 0))
);


--
-- Name: vetting_item_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vetting_item_types (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    standard_id uuid NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    description text,
    evidence_kind text DEFAULT 'any'::text NOT NULL,
    required boolean DEFAULT true NOT NULL,
    sort_order smallint DEFAULT 100 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vetting_item_types_evidence_kind_check CHECK ((evidence_kind = ANY (ARRAY['file'::text, 'note'::text, 'any'::text]))),
    CONSTRAINT vetting_item_types_key_check CHECK ((key ~ '^[a-z][a-z0-9_]*$'::text))
);


--
-- Name: vetting_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vetting_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    coach_id uuid NOT NULL,
    item_type text NOT NULL,
    evidence_location text,
    notes text,
    verified_by uuid,
    verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    item_type_id uuid,
    CONSTRAINT vetting_items_check CHECK (((verified_by IS NULL) = (verified_at IS NULL)))
);


--
-- Name: vetting_standards; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vetting_standards (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version integer NOT NULL,
    title text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    activated_by uuid,
    activated_at timestamp with time zone,
    retired_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vetting_standards_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT vetting_standards_version_check CHECK ((version >= 1))
);


--
-- Name: worker_heartbeats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.worker_heartbeats (
    worker_id text NOT NULL,
    host text NOT NULL,
    git_sha text,
    started_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    current_job_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: actors actors_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actors
    ADD CONSTRAINT actors_name_key UNIQUE (name);


--
-- Name: actors actors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actors
    ADD CONSTRAINT actors_pkey PRIMARY KEY (id);


--
-- Name: ad_daily ad_daily_ad_id_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_daily
    ADD CONSTRAINT ad_daily_ad_id_date_key UNIQUE (ad_id, date);


--
-- Name: ad_daily ad_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_daily
    ADD CONSTRAINT ad_daily_pkey PRIMARY KEY (id);


--
-- Name: ads ads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ads
    ADD CONSTRAINT ads_pkey PRIMARY KEY (id);


--
-- Name: ads ads_platform_platform_ad_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ads
    ADD CONSTRAINT ads_platform_platform_ad_id_key UNIQUE (platform, platform_ad_id);


--
-- Name: approvals approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_pkey PRIMARY KEY (id);


--
-- Name: bookings bookings_calcom_booking_uid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_calcom_booking_uid_key UNIQUE (calcom_booking_uid);


--
-- Name: bookings bookings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_pkey PRIMARY KEY (id);


--
-- Name: call_extractions call_extractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_extractions
    ADD CONSTRAINT call_extractions_pkey PRIMARY KEY (id);


--
-- Name: calls calls_close_activity_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_close_activity_id_key UNIQUE (close_activity_id);


--
-- Name: calls calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_pkey PRIMARY KEY (id);


--
-- Name: coach_need_states coach_need_states_coach_id_need_state_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coach_need_states
    ADD CONSTRAINT coach_need_states_coach_id_need_state_key_key UNIQUE (coach_id, need_state_key);


--
-- Name: coach_need_states coach_need_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coach_need_states
    ADD CONSTRAINT coach_need_states_pkey PRIMARY KEY (id);


--
-- Name: coaches coaches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coaches
    ADD CONSTRAINT coaches_pkey PRIMARY KEY (id);


--
-- Name: commission_adjustments commission_adjustments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission_adjustments
    ADD CONSTRAINT commission_adjustments_pkey PRIMARY KEY (id);


--
-- Name: commissions commissions_outcome_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_outcome_id_key UNIQUE (outcome_id);


--
-- Name: commissions commissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_pkey PRIMARY KEY (id);


--
-- Name: consent_records consent_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consent_records
    ADD CONSTRAINT consent_records_pkey PRIMARY KEY (id);


--
-- Name: console_sessions console_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_sessions
    ADD CONSTRAINT console_sessions_pkey PRIMARY KEY (id);


--
-- Name: console_sessions console_sessions_token_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_sessions
    ADD CONSTRAINT console_sessions_token_sha256_key UNIQUE (token_sha256);


--
-- Name: console_users console_users_actor_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_users
    ADD CONSTRAINT console_users_actor_id_key UNIQUE (actor_id);


--
-- Name: console_users console_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_users
    ADD CONSTRAINT console_users_pkey PRIMARY KEY (id);


--
-- Name: console_users console_users_tailscale_login_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_users
    ADD CONSTRAINT console_users_tailscale_login_key UNIQUE (tailscale_login);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: fit_calls fit_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fit_calls
    ADD CONSTRAINT fit_calls_pkey PRIMARY KEY (id);


--
-- Name: jobs jobs_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);


--
-- Name: leads leads_close_lead_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_close_lead_id_key UNIQUE (close_lead_id);


--
-- Name: leads leads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_pkey PRIMARY KEY (id);


--
-- Name: market_need_states market_need_states_market_key_need_state_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_need_states
    ADD CONSTRAINT market_need_states_market_key_need_state_key_key UNIQUE (market_key, need_state_key);


--
-- Name: market_need_states market_need_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_need_states
    ADD CONSTRAINT market_need_states_pkey PRIMARY KEY (id);


--
-- Name: markets markets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.markets
    ADD CONSTRAINT markets_pkey PRIMARY KEY (key);


--
-- Name: match_candidates match_candidates_match_id_coach_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.match_candidates
    ADD CONSTRAINT match_candidates_match_id_coach_id_key UNIQUE (match_id, coach_id);


--
-- Name: match_candidates match_candidates_match_id_rank_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.match_candidates
    ADD CONSTRAINT match_candidates_match_id_rank_key UNIQUE (match_id, rank);


--
-- Name: match_candidates match_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.match_candidates
    ADD CONSTRAINT match_candidates_pkey PRIMARY KEY (id);


--
-- Name: matches matches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_pkey PRIMARY KEY (id);


--
-- Name: need_states need_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.need_states
    ADD CONSTRAINT need_states_pkey PRIMARY KEY (key);


--
-- Name: outbound_messages outbound_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_messages
    ADD CONSTRAINT outbound_messages_pkey PRIMARY KEY (id);


--
-- Name: outcomes outcomes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT outcomes_pkey PRIMARY KEY (id);


--
-- Name: safety_flags safety_flags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.safety_flags
    ADD CONSTRAINT safety_flags_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: system_job_types system_job_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_job_types
    ADD CONSTRAINT system_job_types_pkey PRIMARY KEY (job_type);


--
-- Name: systems systems_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.systems
    ADD CONSTRAINT systems_pkey PRIMARY KEY (key);


--
-- Name: templates templates_key_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.templates
    ADD CONSTRAINT templates_key_version_key UNIQUE (key, version);


--
-- Name: templates templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.templates
    ADD CONSTRAINT templates_pkey PRIMARY KEY (id);


--
-- Name: vetting_evidence vetting_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_evidence
    ADD CONSTRAINT vetting_evidence_pkey PRIMARY KEY (id);


--
-- Name: vetting_item_types vetting_item_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_item_types
    ADD CONSTRAINT vetting_item_types_pkey PRIMARY KEY (id);


--
-- Name: vetting_item_types vetting_item_types_standard_id_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_item_types
    ADD CONSTRAINT vetting_item_types_standard_id_key_key UNIQUE (standard_id, key);


--
-- Name: vetting_items vetting_items_coach_id_item_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_items
    ADD CONSTRAINT vetting_items_coach_id_item_type_key UNIQUE (coach_id, item_type);


--
-- Name: vetting_items vetting_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_items
    ADD CONSTRAINT vetting_items_pkey PRIMARY KEY (id);


--
-- Name: vetting_standards vetting_standards_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_standards
    ADD CONSTRAINT vetting_standards_pkey PRIMARY KEY (id);


--
-- Name: vetting_standards vetting_standards_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_standards
    ADD CONSTRAINT vetting_standards_version_key UNIQUE (version);


--
-- Name: worker_heartbeats worker_heartbeats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_heartbeats
    ADD CONSTRAINT worker_heartbeats_pkey PRIMARY KEY (worker_id);


--
-- Name: approvals_subject; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX approvals_subject ON public.approvals USING btree (subject_type, subject_id);


--
-- Name: bookings_lead; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX bookings_lead ON public.bookings USING btree (lead_id);


--
-- Name: call_extractions_call; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX call_extractions_call ON public.call_extractions USING btree (call_id, field_name);


--
-- Name: calls_lead; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX calls_lead ON public.calls USING btree (lead_id);


--
-- Name: calls_purge; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX calls_purge ON public.calls USING btree (purge_after) WHERE (purged_at IS NULL);


--
-- Name: coaches_market_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX coaches_market_status ON public.coaches USING btree (market_key, status);


--
-- Name: consent_records_lead; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX consent_records_lead ON public.consent_records USING btree (lead_id, created_at);


--
-- Name: console_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX console_sessions_user ON public.console_sessions USING btree (console_user_id) WHERE (revoked_at IS NULL);


--
-- Name: events_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX events_entity ON public.events USING btree (entity_type, entity_id, id);


--
-- Name: events_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX events_job ON public.events USING btree (job_id) WHERE (job_id IS NOT NULL);


--
-- Name: jobs_claimable; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_claimable ON public.jobs USING btree (priority, run_after) WHERE (status = 'pending'::text);


--
-- Name: jobs_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_entity ON public.jobs USING btree (entity_type, entity_id);


--
-- Name: jobs_running_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX jobs_running_lease ON public.jobs USING btree (lease_expires_at) WHERE (status = 'running'::text);


--
-- Name: leads_dedupe; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX leads_dedupe ON public.leads USING btree (dedupe_key) WHERE (dedupe_key IS NOT NULL);


--
-- Name: leads_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX leads_status ON public.leads USING btree (status);


--
-- Name: matches_lead; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX matches_lead ON public.matches USING btree (lead_id);


--
-- Name: outbound_messages_lead; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX outbound_messages_lead ON public.outbound_messages USING btree (lead_id) WHERE (lead_id IS NOT NULL);


--
-- Name: safety_flags_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX safety_flags_open ON public.safety_flags USING btree (lead_id) WHERE (cleared_at IS NULL);


--
-- Name: vetting_evidence_item; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vetting_evidence_item ON public.vetting_evidence USING btree (vetting_item_id);


--
-- Name: vetting_standards_one_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX vetting_standards_one_active ON public.vetting_standards USING btree (status) WHERE (status = 'active'::text);


--
-- Name: actors actors_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER actors_events AFTER INSERT OR DELETE OR UPDATE ON public.actors FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: actors actors_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER actors_updated_at BEFORE UPDATE ON public.actors FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: ad_daily ad_daily_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER ad_daily_events AFTER INSERT OR DELETE OR UPDATE ON public.ad_daily FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: ad_daily ad_daily_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER ad_daily_updated_at BEFORE UPDATE ON public.ad_daily FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: ads ads_default_market; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER ads_default_market BEFORE INSERT ON public.ads FOR EACH ROW EXECUTE FUNCTION public.ledger_default_market();


--
-- Name: ads ads_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER ads_events AFTER INSERT OR DELETE OR UPDATE ON public.ads FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: ads ads_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER ads_updated_at BEFORE UPDATE ON public.ads FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: approvals approvals_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER approvals_events AFTER INSERT OR DELETE OR UPDATE ON public.approvals FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'decision_note');


--
-- Name: approvals approvals_founder; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER approvals_founder BEFORE INSERT OR UPDATE ON public.approvals FOR EACH ROW EXECUTE FUNCTION public.approvals_guard();


--
-- Name: approvals approvals_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER approvals_updated_at BEFORE UPDATE ON public.approvals FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: bookings bookings_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER bookings_events AFTER INSERT OR DELETE OR UPDATE ON public.bookings FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: bookings bookings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER bookings_updated_at BEFORE UPDATE ON public.bookings FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: call_extractions call_extractions_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER call_extractions_events AFTER INSERT OR DELETE OR UPDATE ON public.call_extractions FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'value_json', 'value_ref', 'corrected_value_json', 'corrected_value_ref');


--
-- Name: call_extractions call_extractions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER call_extractions_updated_at BEFORE UPDATE ON public.call_extractions FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: calls calls_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER calls_events AFTER INSERT OR DELETE OR UPDATE ON public.calls FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: calls calls_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER calls_updated_at BEFORE UPDATE ON public.calls FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: coach_need_states coach_need_states_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER coach_need_states_events AFTER INSERT OR DELETE OR UPDATE ON public.coach_need_states FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: coach_need_states coach_need_states_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER coach_need_states_updated_at BEFORE UPDATE ON public.coach_need_states FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: coaches coaches_default_market; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER coaches_default_market BEFORE INSERT ON public.coaches FOR EACH ROW EXECUTE FUNCTION public.ledger_default_market();


--
-- Name: coaches coaches_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER coaches_events AFTER INSERT OR DELETE OR UPDATE ON public.coaches FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'legal_name', 'email', 'phone', 'notes');


--
-- Name: coaches coaches_status; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER coaches_status BEFORE INSERT OR UPDATE ON public.coaches FOR EACH ROW EXECUTE FUNCTION public.coaches_status_guard();


--
-- Name: coaches coaches_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER coaches_updated_at BEFORE UPDATE ON public.coaches FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: commission_adjustments commission_adjustments_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER commission_adjustments_events AFTER INSERT OR DELETE OR UPDATE ON public.commission_adjustments FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'reason');


--
-- Name: commission_adjustments commission_adjustments_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER commission_adjustments_updated_at BEFORE UPDATE ON public.commission_adjustments FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: commissions commissions_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER commissions_events AFTER INSERT OR DELETE OR UPDATE ON public.commissions FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: commissions commissions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER commissions_updated_at BEFORE UPDATE ON public.commissions FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: consent_records consent_records_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER consent_records_append_only BEFORE DELETE OR UPDATE ON public.consent_records FOR EACH ROW EXECUTE FUNCTION public.ledger_forbid_change();


--
-- Name: consent_records consent_records_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER consent_records_events AFTER INSERT OR DELETE OR UPDATE ON public.consent_records FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'evidence_ref');


--
-- Name: consent_records consent_records_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER consent_records_updated_at BEFORE UPDATE ON public.consent_records FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: console_sessions console_sessions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER console_sessions_updated_at BEFORE UPDATE ON public.console_sessions FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: console_users console_users_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER console_users_events AFTER INSERT OR DELETE OR UPDATE ON public.console_users FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'tailscale_login', 'password_hash');


--
-- Name: console_users console_users_founder; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER console_users_founder BEFORE INSERT OR UPDATE OF actor_id ON public.console_users FOR EACH ROW EXECUTE FUNCTION public.console_users_guard();


--
-- Name: console_users console_users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER console_users_updated_at BEFORE UPDATE ON public.console_users FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: events events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER events_append_only BEFORE DELETE OR UPDATE ON public.events FOR EACH ROW EXECUTE FUNCTION public.ledger_forbid_change();


--
-- Name: events events_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON public.events FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_forbid_change();


--
-- Name: fit_calls fit_calls_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER fit_calls_events AFTER INSERT OR DELETE OR UPDATE ON public.fit_calls FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: fit_calls fit_calls_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER fit_calls_updated_at BEFORE UPDATE ON public.fit_calls FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: jobs jobs_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER jobs_events AFTER INSERT OR DELETE OR UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'payload', 'result_ref', 'error_detail');


--
-- Name: jobs jobs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER jobs_updated_at BEFORE UPDATE ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: leads leads_default_market; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER leads_default_market BEFORE INSERT ON public.leads FOR EACH ROW EXECUTE FUNCTION public.ledger_default_market();


--
-- Name: leads leads_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER leads_events AFTER INSERT OR DELETE OR UPDATE ON public.leads FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'first_name', 'phone_e164', 'email', 'intake_note');


--
-- Name: leads leads_safety_hold; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER leads_safety_hold BEFORE INSERT OR UPDATE ON public.leads FOR EACH ROW EXECUTE FUNCTION public.leads_safety_hold_guard();


--
-- Name: leads leads_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER leads_updated_at BEFORE UPDATE ON public.leads FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: market_need_states market_need_states_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER market_need_states_events AFTER INSERT OR DELETE OR UPDATE ON public.market_need_states FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: market_need_states market_need_states_rule_writer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER market_need_states_rule_writer BEFORE INSERT OR DELETE OR UPDATE ON public.market_need_states FOR EACH ROW EXECUTE FUNCTION public.ledger_rule_writer_guard();


--
-- Name: market_need_states market_need_states_stamp; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER market_need_states_stamp BEFORE INSERT OR UPDATE ON public.market_need_states FOR EACH ROW EXECUTE FUNCTION public.market_need_states_stamp();


--
-- Name: market_need_states market_need_states_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER market_need_states_updated_at BEFORE UPDATE ON public.market_need_states FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: markets markets_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER markets_events AFTER INSERT OR DELETE OR UPDATE ON public.markets FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('key');


--
-- Name: markets markets_rule_writer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER markets_rule_writer BEFORE INSERT OR DELETE OR UPDATE ON public.markets FOR EACH ROW EXECUTE FUNCTION public.ledger_rule_writer_guard();


--
-- Name: markets markets_time_zone; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER markets_time_zone BEFORE INSERT OR UPDATE OF time_zone ON public.markets FOR EACH ROW EXECUTE FUNCTION public.markets_time_zone_guard();


--
-- Name: markets markets_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER markets_updated_at BEFORE UPDATE ON public.markets FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: match_candidates match_candidates_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER match_candidates_events AFTER INSERT OR DELETE OR UPDATE ON public.match_candidates FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'reasons');


--
-- Name: match_candidates match_candidates_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER match_candidates_updated_at BEFORE UPDATE ON public.match_candidates FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: matches matches_confirm; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER matches_confirm BEFORE INSERT OR UPDATE ON public.matches FOR EACH ROW EXECUTE FUNCTION public.matches_confirm_guard();


--
-- Name: matches matches_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER matches_events AFTER INSERT OR DELETE OR UPDATE ON public.matches FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'override_reason');


--
-- Name: matches matches_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER matches_updated_at BEFORE UPDATE ON public.matches FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: need_states need_states_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER need_states_events AFTER INSERT OR DELETE OR UPDATE ON public.need_states FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('key');


--
-- Name: need_states need_states_rule_writer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER need_states_rule_writer BEFORE INSERT OR DELETE OR UPDATE ON public.need_states FOR EACH ROW EXECUTE FUNCTION public.ledger_rule_writer_guard();


--
-- Name: need_states need_states_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER need_states_updated_at BEFORE UPDATE ON public.need_states FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: outbound_messages outbound_messages_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER outbound_messages_events AFTER INSERT OR DELETE OR UPDATE ON public.outbound_messages FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: outbound_messages outbound_messages_send_gate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER outbound_messages_send_gate BEFORE INSERT OR UPDATE ON public.outbound_messages FOR EACH ROW EXECUTE FUNCTION public.outbound_messages_gate();


--
-- Name: outbound_messages outbound_messages_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER outbound_messages_updated_at BEFORE UPDATE ON public.outbound_messages FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: outcomes outcomes_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER outcomes_events AFTER INSERT OR DELETE OR UPDATE ON public.outcomes FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: outcomes outcomes_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER outcomes_updated_at BEFORE UPDATE ON public.outcomes FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: safety_flags safety_flags_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER safety_flags_events AFTER INSERT OR DELETE OR UPDATE ON public.safety_flags FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'span_ref');


--
-- Name: safety_flags safety_flags_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER safety_flags_updated_at BEFORE UPDATE ON public.safety_flags FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: settings settings_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER settings_events AFTER INSERT OR DELETE OR UPDATE ON public.settings FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('key');


--
-- Name: settings settings_rule_writer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER settings_rule_writer BEFORE INSERT OR DELETE OR UPDATE ON public.settings FOR EACH ROW EXECUTE FUNCTION public.ledger_rule_writer_guard();


--
-- Name: settings settings_stamp; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER settings_stamp BEFORE INSERT OR UPDATE ON public.settings FOR EACH ROW EXECUTE FUNCTION public.settings_stamp();


--
-- Name: settings settings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER settings_updated_at BEFORE UPDATE ON public.settings FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: system_job_types system_job_types_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER system_job_types_events AFTER INSERT OR DELETE OR UPDATE ON public.system_job_types FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('job_type');


--
-- Name: system_job_types system_job_types_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER system_job_types_updated_at BEFORE UPDATE ON public.system_job_types FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: systems systems_enabled; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER systems_enabled BEFORE INSERT OR UPDATE ON public.systems FOR EACH ROW EXECUTE FUNCTION public.systems_guard();


--
-- Name: systems systems_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER systems_events AFTER INSERT OR DELETE OR UPDATE ON public.systems FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('key');


--
-- Name: systems systems_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER systems_updated_at BEFORE UPDATE ON public.systems FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: templates templates_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER templates_events AFTER INSERT OR DELETE OR UPDATE ON public.templates FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: templates templates_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER templates_immutable BEFORE INSERT OR UPDATE ON public.templates FOR EACH ROW EXECUTE FUNCTION public.templates_guard();


--
-- Name: templates templates_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER templates_updated_at BEFORE UPDATE ON public.templates FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: vetting_evidence vetting_evidence_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_evidence_events AFTER INSERT OR DELETE OR UPDATE ON public.vetting_evidence FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'relative_path', 'display_name', 'note');


--
-- Name: vetting_evidence vetting_evidence_founder; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_evidence_founder BEFORE INSERT OR UPDATE ON public.vetting_evidence FOR EACH ROW EXECUTE FUNCTION public.vetting_evidence_guard();


--
-- Name: vetting_evidence vetting_evidence_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_evidence_updated_at BEFORE UPDATE ON public.vetting_evidence FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: vetting_item_types vetting_item_types_draft_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_item_types_draft_only BEFORE INSERT OR DELETE OR UPDATE ON public.vetting_item_types FOR EACH ROW EXECUTE FUNCTION public.vetting_item_types_guard();


--
-- Name: vetting_item_types vetting_item_types_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_item_types_events AFTER INSERT OR DELETE OR UPDATE ON public.vetting_item_types FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: vetting_item_types vetting_item_types_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_item_types_updated_at BEFORE UPDATE ON public.vetting_item_types FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: vetting_items vetting_items_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_items_events AFTER INSERT OR DELETE OR UPDATE ON public.vetting_items FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id', 'notes', 'evidence_location');


--
-- Name: vetting_items vetting_items_founder; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_items_founder BEFORE INSERT OR UPDATE OF verified_by ON public.vetting_items FOR EACH ROW EXECUTE FUNCTION public.vetting_items_guard();


--
-- Name: vetting_items vetting_items_type; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_items_type BEFORE INSERT OR UPDATE ON public.vetting_items FOR EACH ROW EXECUTE FUNCTION public.vetting_items_type_guard();


--
-- Name: vetting_items vetting_items_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_items_updated_at BEFORE UPDATE ON public.vetting_items FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: vetting_standards vetting_standards_events; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_standards_events AFTER INSERT OR DELETE OR UPDATE ON public.vetting_standards FOR EACH ROW EXECUTE FUNCTION public.ledger_log_change('id');


--
-- Name: vetting_standards vetting_standards_lifecycle; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_standards_lifecycle BEFORE INSERT OR DELETE OR UPDATE ON public.vetting_standards FOR EACH ROW EXECUTE FUNCTION public.vetting_standards_guard();


--
-- Name: vetting_standards vetting_standards_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vetting_standards_updated_at BEFORE UPDATE ON public.vetting_standards FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: worker_heartbeats worker_heartbeats_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER worker_heartbeats_updated_at BEFORE UPDATE ON public.worker_heartbeats FOR EACH ROW EXECUTE FUNCTION public.ledger_touch_updated_at();


--
-- Name: ad_daily ad_daily_ad_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_daily
    ADD CONSTRAINT ad_daily_ad_id_fkey FOREIGN KEY (ad_id) REFERENCES public.ads(id);


--
-- Name: ads ads_market_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ads
    ADD CONSTRAINT ads_market_key_fkey FOREIGN KEY (market_key) REFERENCES public.markets(key);


--
-- Name: ads ads_need_state_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ads
    ADD CONSTRAINT ads_need_state_key_fkey FOREIGN KEY (need_state_key) REFERENCES public.need_states(key);


--
-- Name: approvals approvals_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.actors(id);


--
-- Name: approvals approvals_proposed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT approvals_proposed_by_fkey FOREIGN KEY (proposed_by) REFERENCES public.actors(id);


--
-- Name: bookings bookings_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: call_extractions call_extractions_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_extractions
    ADD CONSTRAINT call_extractions_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id);


--
-- Name: call_extractions call_extractions_corrected_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_extractions
    ADD CONSTRAINT call_extractions_corrected_by_fkey FOREIGN KEY (corrected_by) REFERENCES public.actors(id);


--
-- Name: call_extractions call_extractions_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_extractions
    ADD CONSTRAINT call_extractions_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id);


--
-- Name: calls calls_booking_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bookings(id);


--
-- Name: calls calls_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: calls calls_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.actors(id);


--
-- Name: coach_need_states coach_need_states_coach_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coach_need_states
    ADD CONSTRAINT coach_need_states_coach_id_fkey FOREIGN KEY (coach_id) REFERENCES public.coaches(id);


--
-- Name: coach_need_states coach_need_states_need_state_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coach_need_states
    ADD CONSTRAINT coach_need_states_need_state_key_fkey FOREIGN KEY (need_state_key) REFERENCES public.need_states(key);


--
-- Name: coaches coaches_market_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coaches
    ADD CONSTRAINT coaches_market_key_fkey FOREIGN KEY (market_key) REFERENCES public.markets(key);


--
-- Name: coaches coaches_status_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coaches
    ADD CONSTRAINT coaches_status_changed_by_fkey FOREIGN KEY (status_changed_by) REFERENCES public.actors(id);


--
-- Name: coaches coaches_vetting_standard_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coaches
    ADD CONSTRAINT coaches_vetting_standard_fk FOREIGN KEY (vetting_standard_id) REFERENCES public.vetting_standards(id);


--
-- Name: commission_adjustments commission_adjustments_approval_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission_adjustments
    ADD CONSTRAINT commission_adjustments_approval_id_fkey FOREIGN KEY (approval_id) REFERENCES public.approvals(id);


--
-- Name: commission_adjustments commission_adjustments_commission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission_adjustments
    ADD CONSTRAINT commission_adjustments_commission_id_fkey FOREIGN KEY (commission_id) REFERENCES public.commissions(id);


--
-- Name: commissions commissions_approval_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_approval_id_fkey FOREIGN KEY (approval_id) REFERENCES public.approvals(id);


--
-- Name: commissions commissions_coach_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_coach_id_fkey FOREIGN KEY (coach_id) REFERENCES public.coaches(id);


--
-- Name: commissions commissions_outcome_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commissions
    ADD CONSTRAINT commissions_outcome_id_fkey FOREIGN KEY (outcome_id) REFERENCES public.outcomes(id);


--
-- Name: consent_records consent_records_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.consent_records
    ADD CONSTRAINT consent_records_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: console_sessions console_sessions_console_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_sessions
    ADD CONSTRAINT console_sessions_console_user_id_fkey FOREIGN KEY (console_user_id) REFERENCES public.console_users(id);


--
-- Name: console_users console_users_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_users
    ADD CONSTRAINT console_users_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id);


--
-- Name: events events_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id);


--
-- Name: events events_approval_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_approval_id_fkey FOREIGN KEY (approval_id) REFERENCES public.approvals(id);


--
-- Name: events events_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id);


--
-- Name: fit_calls fit_calls_coach_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fit_calls
    ADD CONSTRAINT fit_calls_coach_id_fkey FOREIGN KEY (coach_id) REFERENCES public.coaches(id);


--
-- Name: fit_calls fit_calls_held_reported_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fit_calls
    ADD CONSTRAINT fit_calls_held_reported_by_fkey FOREIGN KEY (held_reported_by) REFERENCES public.actors(id);


--
-- Name: fit_calls fit_calls_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fit_calls
    ADD CONSTRAINT fit_calls_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: fit_calls fit_calls_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fit_calls
    ADD CONSTRAINT fit_calls_match_id_fkey FOREIGN KEY (match_id) REFERENCES public.matches(id);


--
-- Name: leads leads_ad_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_ad_id_fkey FOREIGN KEY (ad_id) REFERENCES public.ads(id);


--
-- Name: leads leads_market_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_market_key_fkey FOREIGN KEY (market_key) REFERENCES public.markets(key);


--
-- Name: leads leads_merged_into_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_merged_into_lead_id_fkey FOREIGN KEY (merged_into_lead_id) REFERENCES public.leads(id);


--
-- Name: leads leads_need_state_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_need_state_key_fkey FOREIGN KEY (need_state_key) REFERENCES public.need_states(key);


--
-- Name: leads leads_safety_hold_cleared_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_safety_hold_cleared_by_fkey FOREIGN KEY (safety_hold_cleared_by) REFERENCES public.actors(id);


--
-- Name: leads leads_safety_hold_set_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_safety_hold_set_by_fkey FOREIGN KEY (safety_hold_set_by) REFERENCES public.actors(id);


--
-- Name: market_need_states market_need_states_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_need_states
    ADD CONSTRAINT market_need_states_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.actors(id);


--
-- Name: market_need_states market_need_states_market_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_need_states
    ADD CONSTRAINT market_need_states_market_key_fkey FOREIGN KEY (market_key) REFERENCES public.markets(key);


--
-- Name: market_need_states market_need_states_need_state_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_need_states
    ADD CONSTRAINT market_need_states_need_state_key_fkey FOREIGN KEY (need_state_key) REFERENCES public.need_states(key);


--
-- Name: match_candidates match_candidates_coach_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.match_candidates
    ADD CONSTRAINT match_candidates_coach_id_fkey FOREIGN KEY (coach_id) REFERENCES public.coaches(id);


--
-- Name: match_candidates match_candidates_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.match_candidates
    ADD CONSTRAINT match_candidates_match_id_fkey FOREIGN KEY (match_id) REFERENCES public.matches(id);


--
-- Name: matches matches_chosen_coach_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_chosen_coach_id_fkey FOREIGN KEY (chosen_coach_id) REFERENCES public.coaches(id);


--
-- Name: matches matches_confirmed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_confirmed_by_fkey FOREIGN KEY (confirmed_by) REFERENCES public.actors(id);


--
-- Name: matches matches_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id);


--
-- Name: matches matches_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.matches
    ADD CONSTRAINT matches_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: outbound_messages outbound_messages_approval_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_messages
    ADD CONSTRAINT outbound_messages_approval_id_fkey FOREIGN KEY (approval_id) REFERENCES public.approvals(id);


--
-- Name: outbound_messages outbound_messages_coach_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_messages
    ADD CONSTRAINT outbound_messages_coach_id_fkey FOREIGN KEY (coach_id) REFERENCES public.coaches(id);


--
-- Name: outbound_messages outbound_messages_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_messages
    ADD CONSTRAINT outbound_messages_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: outbound_messages outbound_messages_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbound_messages
    ADD CONSTRAINT outbound_messages_template_id_fkey FOREIGN KEY (template_id) REFERENCES public.templates(id);


--
-- Name: outcomes outcomes_coach_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT outcomes_coach_id_fkey FOREIGN KEY (coach_id) REFERENCES public.coaches(id);


--
-- Name: outcomes outcomes_fit_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT outcomes_fit_call_id_fkey FOREIGN KEY (fit_call_id) REFERENCES public.fit_calls(id);


--
-- Name: outcomes outcomes_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT outcomes_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: outcomes outcomes_reported_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outcomes
    ADD CONSTRAINT outcomes_reported_by_fkey FOREIGN KEY (reported_by) REFERENCES public.actors(id);


--
-- Name: safety_flags safety_flags_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.safety_flags
    ADD CONSTRAINT safety_flags_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id);


--
-- Name: safety_flags safety_flags_cleared_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.safety_flags
    ADD CONSTRAINT safety_flags_cleared_by_fkey FOREIGN KEY (cleared_by) REFERENCES public.actors(id);


--
-- Name: safety_flags safety_flags_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.safety_flags
    ADD CONSTRAINT safety_flags_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id);


--
-- Name: safety_flags safety_flags_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.safety_flags
    ADD CONSTRAINT safety_flags_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id);


--
-- Name: settings settings_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.actors(id);


--
-- Name: system_job_types system_job_types_system_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_job_types
    ADD CONSTRAINT system_job_types_system_key_fkey FOREIGN KEY (system_key) REFERENCES public.systems(key);


--
-- Name: systems systems_enabled_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.systems
    ADD CONSTRAINT systems_enabled_changed_by_fkey FOREIGN KEY (enabled_changed_by) REFERENCES public.actors(id);


--
-- Name: templates templates_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.templates
    ADD CONSTRAINT templates_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.actors(id);


--
-- Name: vetting_evidence vetting_evidence_removed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_evidence
    ADD CONSTRAINT vetting_evidence_removed_by_fkey FOREIGN KEY (removed_by) REFERENCES public.actors(id);


--
-- Name: vetting_evidence vetting_evidence_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_evidence
    ADD CONSTRAINT vetting_evidence_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.actors(id);


--
-- Name: vetting_evidence vetting_evidence_vetting_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_evidence
    ADD CONSTRAINT vetting_evidence_vetting_item_id_fkey FOREIGN KEY (vetting_item_id) REFERENCES public.vetting_items(id);


--
-- Name: vetting_item_types vetting_item_types_standard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_item_types
    ADD CONSTRAINT vetting_item_types_standard_id_fkey FOREIGN KEY (standard_id) REFERENCES public.vetting_standards(id);


--
-- Name: vetting_items vetting_items_coach_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_items
    ADD CONSTRAINT vetting_items_coach_id_fkey FOREIGN KEY (coach_id) REFERENCES public.coaches(id);


--
-- Name: vetting_items vetting_items_item_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_items
    ADD CONSTRAINT vetting_items_item_type_id_fkey FOREIGN KEY (item_type_id) REFERENCES public.vetting_item_types(id);


--
-- Name: vetting_items vetting_items_verified_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_items
    ADD CONSTRAINT vetting_items_verified_by_fkey FOREIGN KEY (verified_by) REFERENCES public.actors(id);


--
-- Name: vetting_standards vetting_standards_activated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vetting_standards
    ADD CONSTRAINT vetting_standards_activated_by_fkey FOREIGN KEY (activated_by) REFERENCES public.actors(id);


--
-- Name: worker_heartbeats worker_heartbeats_current_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_heartbeats
    ADD CONSTRAINT worker_heartbeats_current_job_id_fkey FOREIGN KEY (current_job_id) REFERENCES public.jobs(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20261010000001'),
    ('20261013000001');
