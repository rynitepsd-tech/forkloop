-- Kanboard v1.2.54 SQLite schema (PRAGMA user_version 128), dumped from the kanboard-v1 image
-- (worlds/kanboard_v1/docker). Kanboard is MIT licensed: https://github.com/kanboard/kanboard
-- Used offline only: the fake-backend stand-in the oracle tests run against (tests/test_kanboard_world.py).

CREATE TABLE action_has_params (
            id INTEGER PRIMARY KEY,
            action_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            FOREIGN KEY(action_id) REFERENCES actions(id) ON DELETE CASCADE
        );

CREATE TABLE actions (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            event_name TEXT NOT NULL,
            action_name TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

CREATE TABLE column_has_move_restrictions (
            restriction_id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            src_column_id INTEGER NOT NULL,
            dst_column_id INTEGER NOT NULL, only_assigned INTEGER DEFAULT 0,
            UNIQUE(role_id, src_column_id, dst_column_id),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(role_id) REFERENCES project_has_roles(role_id) ON DELETE CASCADE,
            FOREIGN KEY(src_column_id) REFERENCES columns(id) ON DELETE CASCADE,
            FOREIGN KEY(dst_column_id) REFERENCES columns(id) ON DELETE CASCADE
        );

CREATE TABLE column_has_restrictions (
            restriction_id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            column_id INTEGER NOT NULL,
            rule VARCHAR(255) NOT NULL,
            UNIQUE(role_id, column_id, rule),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(role_id) REFERENCES project_has_roles(role_id) ON DELETE CASCADE,
            FOREIGN KEY(column_id) REFERENCES columns(id) ON DELETE CASCADE
        );

CREATE TABLE columns (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            position INTEGER,
            project_id INTEGER NOT NULL, task_limit INTEGER DEFAULT '0', description TEXT, hide_in_dashboard INTEGER DEFAULT 0 NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE (title, project_id)
        );

CREATE TABLE comments (
            id INTEGER PRIMARY KEY,
            task_id INTEGER NOT NULL,
            user_id INTEGER DEFAULT 0,
            date_creation INTEGER NOT NULL,
            comment TEXT NOT NULL,
            reference VARCHAR(50), date_modification INTEGER, visibility VARCHAR(25) NOT NULL DEFAULT 'app-user',
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

CREATE TABLE currencies ("currency" TEXT NOT NULL UNIQUE, "rate" REAL DEFAULT 0);

CREATE TABLE custom_filters (
            id INTEGER PRIMARY KEY,
            filter TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_shared INTEGER DEFAULT 0
        , append INTEGER DEFAULT 0);

CREATE TABLE group_has_users (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(group_id, user_id)
        );

CREATE TABLE groups (
            id INTEGER PRIMARY KEY,
            external_id TEXT DEFAULT '',
            name TEXT NOCASE NOT NULL UNIQUE
        );

CREATE TABLE invites (
            email TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            token TEXT NOT NULL,
            PRIMARY KEY(email, token)
        );

CREATE TABLE last_logins (
            id INTEGER PRIMARY KEY,
            auth_type TEXT,
            user_id INTEGER NOT NULL,
            ip TEXT,
            user_agent TEXT,
            date_creation INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

CREATE TABLE links (
        id INTEGER PRIMARY KEY,
        label TEXT NOT NULL,
        opposite_id INTEGER DEFAULT 0,
        UNIQUE(label)
    );

CREATE TABLE password_reset (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            date_expiration INTEGER NOT NULL,
            date_creation INTEGER NOT NULL,
            ip TEXT NOT NULL,
            user_agent TEXT NOT NULL,
            is_active INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

CREATE TABLE plugin_schema_versions (
            plugin TEXT NOT NULL PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 0
        );

CREATE TABLE predefined_task_descriptions (
        id INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    );

CREATE TABLE project_activities (
          id INTEGER PRIMARY KEY,
          date_creation INTEGER NOT NULL,
          event_name TEXT NOT NULL,
          creator_id INTEGER NOT NULL,
          project_id INTEGER NOT NULL,
          task_id INTEGER NOT NULL,
          data TEXT,
          FOREIGN KEY(creator_id) REFERENCES users(id) ON DELETE CASCADE,
          FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
          FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
      );

CREATE TABLE "project_daily_column_stats" (
            id INTEGER PRIMARY KEY,
            day TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            column_id INTEGER NOT NULL,
            total INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(column_id) REFERENCES columns(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

CREATE TABLE project_daily_stats (
            id INTEGER PRIMARY KEY,
            day TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            avg_lead_time INTEGER NOT NULL DEFAULT 0,
            avg_cycle_time INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

CREATE TABLE project_has_categories (
            id INTEGER PRIMARY KEY,
            name TEXT COLLATE NOCASE NOT NULL,
            project_id INTEGER NOT NULL, description TEXT, color_id TEXT DEFAULT NULL,
            UNIQUE (project_id, name),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

CREATE TABLE project_has_files (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            name TEXT COLLATE NOCASE NOT NULL,
            path TEXT NOT NULL,
            is_image INTEGER DEFAULT 0,
            size INTEGER DEFAULT 0 NOT NULL,
            user_id INTEGER DEFAULT 0 NOT NULL,
            date INTEGER DEFAULT 0 NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

CREATE TABLE project_has_groups (
            group_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(group_id, project_id)
        );

CREATE TABLE project_has_metadata (
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            value TEXT DEFAULT '', changed_by INTEGER DEFAULT 0 NOT NULL, changed_on INTEGER DEFAULT 0 NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, name)
        );

CREATE TABLE project_has_notification_types (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            notification_type TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, notification_type)
        );

CREATE TABLE project_has_roles (
            role_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            UNIQUE(project_id, role),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

CREATE TABLE project_has_users (
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, is_owner INTEGER DEFAULT "0", role TEXT NOT NULL DEFAULT 'project-viewer',
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(project_id, user_id)
        );

CREATE TABLE project_role_has_restrictions (
            restriction_id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            rule VARCHAR(255) NOT NULL,
            UNIQUE(role_id, rule),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(role_id) REFERENCES project_has_roles(role_id) ON DELETE CASCADE
        );

CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            name TEXT NOCASE NOT NULL,
            is_active INTEGER DEFAULT 1
        , token TEXT, last_modified INTEGER DEFAULT 0, is_public INTEGER DEFAULT "0", is_private INTEGER DEFAULT "0", is_everybody_allowed INTEGER DEFAULT "0", default_swimlane TEXT DEFAULT 'Default swimlane', show_default_swimlane INTEGER DEFAULT 1, description TEXT, identifier TEXT DEFAULT '', start_date TEXT DEFAULT '', end_date TEXT DEFAULT '', owner_id INTEGER DEFAULT 0, priority_default INTEGER DEFAULT 0, priority_start INTEGER DEFAULT 0, priority_end INTEGER DEFAULT 3, email TEXT, predefined_email_subjects TEXT, per_swimlane_task_limits INTEGER DEFAULT 0 NOT NULL, task_limit INTEGER DEFAULT 0, enable_global_tags INTEGER DEFAULT 1 NOT NULL);

CREATE TABLE remember_me (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            ip TEXT,
            user_agent TEXT,
            token TEXT,
            sequence TEXT,
            expiration INTEGER,
            date_creation INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        expire_at INTEGER NOT NULL,
        data TEXT DEFAULT ''
    );

CREATE TABLE settings (
            option TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        , changed_by INTEGER DEFAULT 0 NOT NULL, changed_on INTEGER DEFAULT 0 NOT NULL);

CREATE TABLE subtask_time_tracking (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            subtask_id INTEGER NOT NULL,
            start INTEGER DEFAULT 0,
            end INTEGER DEFAULT 0,
            time_spent REAL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(subtask_id) REFERENCES subtasks(id) ON DELETE CASCADE
        );

CREATE TABLE "subtasks" (
            id INTEGER PRIMARY KEY,
            title TEXT COLLATE NOCASE NOT NULL,
            status INTEGER DEFAULT 0,
            time_estimated NUMERIC DEFAULT 0,
            time_spent NUMERIC DEFAULT 0,
            task_id INTEGER NOT NULL,
            user_id INTEGER, position INTEGER DEFAULT 1,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

CREATE TABLE swimlanes (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            position INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            project_id INTEGER NOT NULL, description TEXT, task_limit INTEGER DEFAULT 0,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE (name, project_id)
        );

CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            project_id INTEGER NOT NULL, color_id TEXT DEFAULT NULL,
            UNIQUE(project_id, name)
        );

CREATE TABLE task_has_external_links (
            id INTEGER PRIMARY KEY,
            link_type TEXT NOT NULL,
            dependency TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            date_creation INTEGER NOT NULL,
            date_modification INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            creator_id INTEGER DEFAULT 0,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

CREATE TABLE "task_has_files" (
            id INTEGER PRIMARY KEY,
            name TEXT COLLATE NOCASE NOT NULL,
            path TEXT,
            is_image INTEGER DEFAULT 0,
            task_id INTEGER NOT NULL, "date" INTEGER NOT NULL DEFAULT 0, "user_id" INTEGER NOT NULL DEFAULT 0, "size" INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

CREATE TABLE task_has_links (
        id INTEGER PRIMARY KEY,
        link_id INTEGER NOT NULL,
        task_id INTEGER NOT NULL,
        opposite_task_id INTEGER NOT NULL,
        FOREIGN KEY(link_id) REFERENCES links(id) ON DELETE CASCADE,
        FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
        FOREIGN KEY(opposite_task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );

CREATE TABLE task_has_metadata (
            task_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            value TEXT DEFAULT '', changed_by INTEGER DEFAULT 0 NOT NULL, changed_on INTEGER DEFAULT 0 NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            UNIQUE(task_id, name)
        );

CREATE TABLE task_has_tags (
            task_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE,
            UNIQUE(tag_id, task_id)
        );

CREATE TABLE "tasks"
        (
            id                   INTEGER PRIMARY KEY,
            title                TEXT NOCASE NOT NULL,
            description          TEXT,
            date_creation        INTEGER,
            color_id             TEXT,
            project_id           INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            column_id            INTEGER REFERENCES columns(id) ON DELETE CASCADE,
            owner_id             INTEGER DEFAULT '0',
            position             INTEGER,
            is_active            INTEGER DEFAULT 1,
            date_completed       INTEGER,
            score                INTEGER,
            date_due             INTEGER,
            category_id          INTEGER DEFAULT 0,
            creator_id           INTEGER DEFAULT '0',
            date_modification    INTEGER DEFAULT '0',
            reference            TEXT    DEFAULT '',
            date_started         INTEGER,
            time_spent           NUMERIC DEFAULT 0,
            time_estimated       NUMERIC DEFAULT 0,
            swimlane_id          INTEGER REFERENCES swimlanes(id) ON DELETE CASCADE,
            date_moved           INTEGER DEFAULT 0,
            recurrence_status    INTEGER DEFAULT 0 NOT NULL,
            recurrence_trigger   INTEGER DEFAULT 0 NOT NULL,
            recurrence_factor    INTEGER DEFAULT 0 NOT NULL,
            recurrence_timeframe INTEGER DEFAULT 0 NOT NULL,
            recurrence_basedate  INTEGER DEFAULT 0 NOT NULL,
            recurrence_parent    INTEGER,
            recurrence_child     INTEGER,
            priority             INTEGER DEFAULT 0,
            external_provider    TEXT,
            external_uri         TEXT
        );

CREATE TABLE transitions (
        "id" INTEGER PRIMARY KEY,
        "user_id" INTEGER NOT NULL,
        "project_id" INTEGER NOT NULL,
        "task_id" INTEGER NOT NULL,
        "src_column_id" INTEGER NOT NULL,
        "dst_column_id" INTEGER NOT NULL,
        "date" INTEGER NOT NULL,
        "time_spent" INTEGER DEFAULT 0,
        FOREIGN KEY(src_column_id) REFERENCES columns(id) ON DELETE CASCADE,
        FOREIGN KEY(dst_column_id) REFERENCES columns(id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );

CREATE TABLE user_has_metadata (
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            value TEXT DEFAULT '', changed_by INTEGER DEFAULT 0 NOT NULL, changed_on INTEGER DEFAULT 0 NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, name)
        );

CREATE TABLE user_has_notification_types (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            notification_type TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

CREATE TABLE user_has_notifications (
            user_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, user_id)
        );

CREATE TABLE user_has_unread_notifications (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            date_creation INTEGER NOT NULL,
            event_name TEXT NOT NULL,
            event_data TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            password TEXT,
            is_admin INTEGER DEFAULT 0
        , is_ldap_user INTEGER DEFAULT 0, name TEXT, email TEXT, google_id TEXT, github_id TEXT, notifications_enabled INTEGER DEFAULT '0', timezone TEXT, language TEXT, disable_login_form INTEGER DEFAULT 0, twofactor_activated INTEGER DEFAULT 0, twofactor_secret TEXT, token TEXT DEFAULT '', notifications_filter INTEGER DEFAULT 4, nb_failed_login INTEGER DEFAULT 0, lock_expiration_date INTEGER DEFAULT 0, is_project_admin INTEGER DEFAULT 0, gitlab_id INTEGER, role TEXT NOT NULL DEFAULT 'app-user', is_active INTEGER DEFAULT 1, avatar_path TEXT, api_access_token VARCHAR(255) DEFAULT NULL, filter TEXT, theme TEXT DEFAULT 'light' NOT NULL);

CREATE INDEX categories_project_idx ON project_has_categories(project_id);

CREATE INDEX columns_project_idx ON columns(project_id);

CREATE INDEX files_task_idx ON "task_has_files"(task_id);

CREATE INDEX last_logins_user_idx ON last_logins(user_id);

CREATE UNIQUE INDEX project_daily_column_stats_idx ON "project_daily_column_stats"(day, project_id, column_id);

CREATE UNIQUE INDEX project_daily_stats_idx ON project_daily_stats(day, project_id);

CREATE INDEX subtasks_task_idx ON subtasks(task_id);

CREATE INDEX swimlanes_project_idx ON swimlanes(project_id);

CREATE INDEX task_has_links_task_index ON task_has_links(task_id);

CREATE UNIQUE INDEX task_has_links_unique ON task_has_links(link_id, task_id, opposite_task_id);

CREATE INDEX transitions_project_index ON transitions(project_id);

CREATE INDEX transitions_task_index ON transitions(task_id);

CREATE INDEX transitions_user_index ON transitions(user_id);

CREATE UNIQUE INDEX user_has_notification_types_user_idx ON user_has_notification_types(user_id, notification_type);

CREATE INDEX users_admin_idx ON users(is_admin);

CREATE UNIQUE INDEX users_username_idx ON users(username);

