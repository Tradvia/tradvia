-- SPDX-License-Identifier: FSL-1.1-ALv2
-- Idempotent: applied by the API on the first successful database connection.

CREATE TABLE IF NOT EXISTS deliveries (
    id          text PRIMARY KEY,
    event       text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS issues (
    id          bigint PRIMARY KEY,
    number      integer NOT NULL,
    title       text NOT NULL,
    state       text NOT NULL,
    labels      text[] NOT NULL DEFAULT '{}',
    author      text,
    created_at  timestamptz,
    updated_at  timestamptz NOT NULL,
    closed_at   timestamptz
);

CREATE TABLE IF NOT EXISTS comments (
    id                 bigint PRIMARY KEY,
    issue_number       integer NOT NULL,
    author             text,
    author_association text,
    body               text NOT NULL DEFAULT '',
    created_at         timestamptz,
    updated_at         timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS pull_requests (
    id          bigint PRIMARY KEY,
    number      integer NOT NULL,
    title       text NOT NULL,
    state       text NOT NULL,
    merged      boolean NOT NULL DEFAULT false,
    branch      text,
    author      text,
    created_at  timestamptz,
    updated_at  timestamptz NOT NULL,
    merged_at   timestamptz
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id           bigint PRIMARY KEY,
    workflow     text NOT NULL,
    event        text,
    status       text,
    conclusion   text,
    issue_number integer,
    started_at   timestamptz,
    ended_at     timestamptz,
    updated_at   timestamptz NOT NULL
);

ALTER TABLE issues ADD COLUMN IF NOT EXISTS html_url text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS state_reason text;
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS html_url text;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS display_title text;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS html_url text;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS run_attempt integer;

CREATE INDEX IF NOT EXISTS comments_issue_idx ON comments (issue_number);
CREATE INDEX IF NOT EXISTS workflow_runs_issue_idx ON workflow_runs (issue_number);
