CREATE TABLE "account_approvals" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"origin" text NOT NULL,
	"scope" text NOT NULL,
	"approved_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "job_forms" (
	"job_id" text PRIMARY KEY NOT NULL,
	"inventory" jsonb,
	"plan" jsonb,
	"missing" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"requirements" jsonb,
	"fill_done_at" timestamp with time zone,
	"verified_at" timestamp with time zone,
	"draft_hash" text,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "job_forms" ADD CONSTRAINT "job_forms_job_id_jobs_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE no action ON UPDATE no action;