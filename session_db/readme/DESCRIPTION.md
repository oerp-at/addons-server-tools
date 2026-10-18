Store sessions in a database instead of the filesystem. This simplifies
the configuration of horizontally scalable deployments, by avoiding the
need for a distributed filesystem to store the Odoo sessions.

Since Odoo 20.0 the session check of the websocket dispatcher (`bus`)
reads the session files directly. When `SESSION_DB_URI` is set, this check
is replaced by one that reads the database store.
