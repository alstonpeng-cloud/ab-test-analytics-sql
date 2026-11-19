PRAGMA foreign_keys = ON;

CREATE TABLE assignments (
    user_id TEXT PRIMARY KEY CHECK (length(user_id) > 0),
    variant TEXT NOT NULL CHECK (variant IN ('control', 'treatment')),
    assigned_at INTEGER NOT NULL CHECK (assigned_at >= 0),
    device_type TEXT NOT NULL CHECK (device_type IN ('android', 'ios'))
);

CREATE TABLE events (
    ingest_id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL CHECK (length(event_id) > 0),
    user_id TEXT NOT NULL REFERENCES assignments(user_id),
    event_name TEXT NOT NULL CHECK (
        event_name IN ('tutorial_complete', 'app_open', 'crash')
    ),
    occurred_at INTEGER NOT NULL CHECK (occurred_at >= 0)
);

CREATE INDEX idx_events_event_id ON events(event_id);
CREATE INDEX idx_events_user_name_time
    ON events(user_id, event_name, occurred_at);
