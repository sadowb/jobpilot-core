-- Transactional invalidation hints only: never copy event payloads into NOTIFY.
CREATE FUNCTION notify_jobpilot_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('jobpilot_events', json_build_object('job', NEW.job_id, 'kind', NEW.kind, 'id', NEW.id)::text);
    RETURN NEW;
END $$;
--> statement-breakpoint
-- A row trigger on the partitioned parent also covers current and future partitions.
CREATE TRIGGER job_events_notify AFTER INSERT ON job_events
FOR EACH ROW EXECUTE FUNCTION notify_jobpilot_event();
--> statement-breakpoint
CREATE FUNCTION notify_jobpilot_automation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('jobpilot_events', json_build_object('job', NULL, 'kind', 'automation', 'id', txid_current())::text);
    RETURN NULL;
END $$;
--> statement-breakpoint
CREATE TRIGGER automation_notify AFTER INSERT OR UPDATE OR DELETE ON automation
FOR EACH STATEMENT EXECUTE FUNCTION notify_jobpilot_automation();
--> statement-breakpoint
CREATE TRIGGER runtime_settings_notify AFTER INSERT OR UPDATE OR DELETE ON runtime_settings
FOR EACH STATEMENT EXECUTE FUNCTION notify_jobpilot_automation();
