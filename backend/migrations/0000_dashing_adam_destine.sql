CREATE TYPE "public"."board_automation" AS ENUM('auto', 'assisted', 'manual');--> statement-breakpoint
CREATE TYPE "public"."confirmation_kind" AS ENUM('live-verified', 'user-confirmed');--> statement-breakpoint
CREATE TYPE "public"."cv_mode" AS ENUM('base', 'tailor');--> statement-breakpoint
CREATE TYPE "public"."job_status" AS ENUM('discovered', 'screened', 'preparing', 'ready', 'awaiting_approval', 'submitted', 'screening', 'interview', 'offer', 'rejected', 'withdrawn', 'closed');--> statement-breakpoint
CREATE TYPE "public"."task_state" AS ENUM('queued', 'leased', 'done', 'failed', 'blocked');--> statement-breakpoint
CREATE TYPE "public"."waiting_on" AS ENUM('agent', 'you', 'site');--> statement-breakpoint
CREATE TABLE "accounts" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"site" text NOT NULL,
	"origin" text NOT NULL,
	"username" text NOT NULL,
	"secret_ref" text NOT NULL,
	"status" text DEFAULT 'active' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"last_used_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "answers" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"question_key" text NOT NULL,
	"answer" text NOT NULL,
	"scope" text DEFAULT 'global' NOT NULL,
	"source" text NOT NULL,
	"confirmed_at" timestamp with time zone NOT NULL
);
--> statement-breakpoint
CREATE TABLE "applications" (
	"job_id" text PRIMARY KEY NOT NULL,
	"applied_on" date NOT NULL,
	"applied_at" timestamp with time zone NOT NULL,
	"cv_version_id" bigint,
	"confirmation" "confirmation_kind" NOT NULL,
	"confirmation_text" text
);
--> statement-breakpoint
CREATE TABLE "approvals" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"kind" text NOT NULL,
	"job_id" text,
	"draft_hash" text NOT NULL,
	"scope" text NOT NULL,
	"approved_at" timestamp with time zone DEFAULT now() NOT NULL,
	"consumed_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "boards" (
	"id" text PRIMARY KEY NOT NULL,
	"display_name" text NOT NULL,
	"automation" "board_automation" DEFAULT 'auto' NOT NULL,
	"max_per_day" integer DEFAULT 50 NOT NULL
);
--> statement-breakpoint
CREATE TABLE "companies" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"slug" text NOT NULL,
	"name" text NOT NULL,
	"website" text,
	"notes" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cost_ledger" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"job_id" text,
	"ts" timestamp with time zone DEFAULT now() NOT NULL,
	"model" text NOT NULL,
	"tokens_in" integer DEFAULT 0 NOT NULL,
	"tokens_out" integer DEFAULT 0 NOT NULL,
	"usd" numeric(10, 4) DEFAULT '0' NOT NULL,
	"step" text,
	"from_cache" boolean DEFAULT false NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cv_tracks" (
	"id" text PRIMARY KEY NOT NULL,
	"title" text NOT NULL,
	"keywords" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"default_mode" "cv_mode" DEFAULT 'base' NOT NULL,
	"current_version_id" bigint
);
--> statement-breakpoint
CREATE TABLE "cv_versions" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"track_id" text,
	"sha256" text NOT NULL,
	"tex_sha256" text NOT NULL,
	"git_commit" text,
	"summary" text,
	"tailored_for_job_id" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "facts" (
	"id" text PRIMARY KEY NOT NULL,
	"claim" text NOT NULL,
	"source" text NOT NULL,
	"confirmed_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "job_events" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"job_id" text NOT NULL,
	"ts" timestamp with time zone DEFAULT now() NOT NULL,
	"kind" text NOT NULL,
	"actor" text NOT NULL,
	"payload" jsonb DEFAULT '{}'::jsonb NOT NULL
);
--> statement-breakpoint
CREATE TABLE "jobs" (
	"id" text PRIMARY KEY NOT NULL,
	"company_id" bigint,
	"role" text NOT NULL,
	"url" text NOT NULL,
	"canonical_url_hash" text NOT NULL,
	"board_id" text,
	"tenant" text,
	"requisition_id" text,
	"company_norm" text NOT NULL,
	"title_norm" text NOT NULL,
	"location_norm" text,
	"location" text,
	"work_mode" text,
	"pay" text,
	"deadline" date,
	"status" "job_status" DEFAULT 'discovered' NOT NULL,
	"waiting_on" "waiting_on" DEFAULT 'agent' NOT NULL,
	"blocked_reason" text,
	"cv_track_id" text,
	"cv_mode" "cv_mode" DEFAULT 'base' NOT NULL,
	"cv_version_id" bigint,
	"match_score" integer,
	"posting_sha256" text,
	"posting_text" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "profile" (
	"key" text PRIMARY KEY NOT NULL,
	"value" jsonb NOT NULL,
	"source" text,
	"confirmed_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "tasks" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"job_id" text NOT NULL,
	"step" text NOT NULL,
	"state" "task_state" DEFAULT 'queued' NOT NULL,
	"priority" integer DEFAULT 0 NOT NULL,
	"attempts" integer DEFAULT 0 NOT NULL,
	"visible_at" timestamp with time zone DEFAULT now() NOT NULL,
	"leased_by" text,
	"heartbeat_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "applications" ADD CONSTRAINT "applications_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "applications" ADD CONSTRAINT "applications_cv_version_id_cv_versions_id_fk" FOREIGN KEY ("cv_version_id") REFERENCES "public"."cv_versions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "approvals" ADD CONSTRAINT "approvals_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cost_ledger" ADD CONSTRAINT "cost_ledger_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cv_versions" ADD CONSTRAINT "cv_versions_track_id_cv_tracks_id_fk" FOREIGN KEY ("track_id") REFERENCES "public"."cv_tracks"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "job_events" ADD CONSTRAINT "job_events_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "jobs" ADD CONSTRAINT "jobs_company_id_companies_id_fk" FOREIGN KEY ("company_id") REFERENCES "public"."companies"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "jobs" ADD CONSTRAINT "jobs_board_id_boards_id_fk" FOREIGN KEY ("board_id") REFERENCES "public"."boards"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "jobs" ADD CONSTRAINT "jobs_cv_track_id_cv_tracks_id_fk" FOREIGN KEY ("cv_track_id") REFERENCES "public"."cv_tracks"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "jobs" ADD CONSTRAINT "jobs_cv_version_id_cv_versions_id_fk" FOREIGN KEY ("cv_version_id") REFERENCES "public"."cv_versions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "tasks" ADD CONSTRAINT "tasks_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "accounts_origin_user_uq" ON "accounts" USING btree ("origin","username");--> statement-breakpoint
CREATE UNIQUE INDEX "answers_key_scope_uq" ON "answers" USING btree ("question_key","scope");--> statement-breakpoint
CREATE UNIQUE INDEX "companies_slug_uq" ON "companies" USING btree ("slug");--> statement-breakpoint
CREATE INDEX "cost_day_idx" ON "cost_ledger" USING btree ("ts");--> statement-breakpoint
CREATE UNIQUE INDEX "cv_versions_sha_uq" ON "cv_versions" USING btree ("sha256");--> statement-breakpoint
CREATE INDEX "job_events_job_idx" ON "job_events" USING btree ("job_id","ts");--> statement-breakpoint
CREATE UNIQUE INDEX "jobs_url_uq" ON "jobs" USING btree ("canonical_url_hash");--> statement-breakpoint
CREATE UNIQUE INDEX "jobs_board_req_uq" ON "jobs" USING btree ("board_id","tenant","requisition_id");--> statement-breakpoint
CREATE INDEX "jobs_fuzzy_idx" ON "jobs" USING btree ("company_norm","title_norm","location_norm");--> statement-breakpoint
CREATE INDEX "jobs_status_idx" ON "jobs" USING btree ("status","waiting_on");--> statement-breakpoint
CREATE INDEX "tasks_claim_idx" ON "tasks" USING btree ("state","visible_at","priority");--> statement-breakpoint
CREATE UNIQUE INDEX "tasks_job_step_uq" ON "tasks" USING btree ("job_id","step");