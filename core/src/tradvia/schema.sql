-- SPDX-License-Identifier: FSL-1.1-ALv2
-- Idempotent: applied by the API on the first successful database connection.
-- Every stored row belongs to a project (docs/projects.md, ADR 0006 phase 0).

CREATE TABLE IF NOT EXISTS projects (
    id          bigserial PRIMARY KEY,
    slug        text NOT NULL UNIQUE,
    kind        text NOT NULL CHECK (kind = 'github_repo'),
    external_id text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    settings    jsonb NOT NULL DEFAULT '{}'
);

INSERT INTO projects (slug, kind, external_id)
VALUES ('tradvia-tradvia', 'github_repo', 'Tradvia/tradvia')
ON CONFLICT DO NOTHING;

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
-- Cost parsed from the PR description (the body itself is not stored).
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS cost_usd numeric;
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS cost_seconds numeric;
-- Tokens parsed from the "Tokens: N in · N out · N cache" line of the PR description.
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS tokens_in bigint;
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS tokens_out bigint;
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS tokens_cache bigint;
-- Cost and tokens of a plan, parsed from the agent's plan comment (one row per plan).
ALTER TABLE comments ADD COLUMN IF NOT EXISTS cost_usd numeric;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS tokens_in bigint;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS tokens_out bigint;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS tokens_cache bigint;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS display_title text;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS html_url text;
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS run_attempt integer;

-- Project scope migration: attach existing rows to the default project, then
-- make project_id mandatory and part of every primary key.
ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects (id);
ALTER TABLE issues ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects (id);
ALTER TABLE comments ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects (id);
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects (id);
ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects (id);

UPDATE deliveries SET project_id = (SELECT id FROM projects WHERE external_id = 'Tradvia/tradvia')
    WHERE project_id IS NULL;
UPDATE issues SET project_id = (SELECT id FROM projects WHERE external_id = 'Tradvia/tradvia')
    WHERE project_id IS NULL;
UPDATE comments SET project_id = (SELECT id FROM projects WHERE external_id = 'Tradvia/tradvia')
    WHERE project_id IS NULL;
UPDATE pull_requests SET project_id = (SELECT id FROM projects WHERE external_id = 'Tradvia/tradvia')
    WHERE project_id IS NULL;
UPDATE workflow_runs SET project_id = (SELECT id FROM projects WHERE external_id = 'Tradvia/tradvia')
    WHERE project_id IS NULL;

ALTER TABLE deliveries ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE issues ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE comments ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE pull_requests ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE workflow_runs ALTER COLUMN project_id SET NOT NULL;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['deliveries', 'issues', 'comments', 'pull_requests', 'workflow_runs']
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_index i
            WHERE i.indrelid = t::regclass AND i.indisprimary AND i.indnatts = 2
        ) THEN
            EXECUTE 'ALTER TABLE ' || quote_ident(t) || ' DROP CONSTRAINT IF EXISTS '
                || quote_ident(t || '_pkey');
            EXECUTE 'ALTER TABLE ' || quote_ident(t) || ' ADD PRIMARY KEY (project_id, id)';
        END IF;
    END LOOP;
END $$;

DROP INDEX IF EXISTS comments_issue_idx;
DROP INDEX IF EXISTS workflow_runs_issue_idx;
CREATE INDEX IF NOT EXISTS comments_project_issue_idx ON comments (project_id, issue_number);
CREATE INDEX IF NOT EXISTS workflow_runs_project_issue_idx
    ON workflow_runs (project_id, issue_number);

CREATE TABLE IF NOT EXISTS backlog_items (
    project_id   bigint NOT NULL REFERENCES projects (id),
    issue_number integer NOT NULL,
    position     integer NOT NULL,
    depends_on   integer[] NOT NULL DEFAULT '{}',
    note         text NOT NULL DEFAULT '',
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, issue_number)
);
CREATE INDEX IF NOT EXISTS backlog_items_position_idx ON backlog_items (project_id, position);

-- Sign in with GitHub (ADR 0008). Not project scoped: a session belongs to a person.
-- `id` is the SHA-256 of the cookie token, so a database leak does not yield live cookies.
CREATE TABLE IF NOT EXISTS sessions (
    id                   text PRIMARY KEY,
    user_id              bigint NOT NULL,
    login                text NOT NULL,
    avatar_url           text,
    token_enc            text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    expires_at           timestamptz NOT NULL,
    absolute_expires_at  timestamptz NOT NULL,
    last_used_at         timestamptz NOT NULL DEFAULT now(),
    role_cache           jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS login_states (
    state         text PRIMARY KEY,
    code_verifier text NOT NULL,
    return_path   text NOT NULL,
    expires_at    timestamptz NOT NULL
);

-- Audit log of actions taken from the panel with the user's own identity (ADR 0008 addendum).
-- Every attempt is recorded, whether it succeeded, was denied or failed.
CREATE TABLE IF NOT EXISTS audit_log (
    id               bigserial PRIMARY KEY,
    project_id       bigint NOT NULL REFERENCES projects (id),
    actor_login      text NOT NULL,
    action           text NOT NULL,
    issue_number     integer,
    result           text NOT NULL,
    detail           text NOT NULL DEFAULT '',
    idempotency_key  text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_log_card_idx ON audit_log (project_id, issue_number, created_at);
CREATE INDEX IF NOT EXISTS audit_log_key_idx ON audit_log (actor_login, idempotency_key, created_at);
