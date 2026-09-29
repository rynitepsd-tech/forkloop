<?php
// kanboard-v1 world configuration (worlds/kanboard_v1/docker/). Everything else is config.default.php.

// SQLite in the container filesystem: `docker commit` (and a fresh container from the golden image)
// carries the whole world, as the Solari rule "everything the agent can touch lives in the snapshot" asks.
define('DB_DRIVER', 'sqlite');
define('DB_RUN_MIGRATIONS', false);          // migrated once at image build (install_world.py)
// Rollback journal, not Kanboard's default WAL (which it runs with wal_autocheckpoint = 0): the whole
// application state is then the one db.sqlite file, and nothing but a transient journal is ever created.
define('DB_WAL_MODE', false);

// No rewrite rules: URLs are /?controller=...&action=...
define('ENABLE_URL_REWRITE', false);
define('LOG_DRIVER', 'file');
define('LOG_FILE', '/var/log/kanboard.log');

// The browser session must survive a container restart (every reset boots the golden image and Chrome
// starts again from its profile): a persistent session cookie + the database session row baked in.
define('SESSION_DURATION', 315360000);       // 10 years
define('SESSION_HANDLER', 'db');
define('REMEMBER_ME_AUTH', true);

// Nothing leaves the container.
define('PLUGIN_INSTALLER', false);
define('MAIL_CONFIGURATION', false);
define('ENABLE_HSTS', false);
define('BRUTEFORCE_CAPTCHA', 1000);
define('BRUTEFORCE_LOCKDOWN', 1000);
