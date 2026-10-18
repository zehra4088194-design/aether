CREATE TABLE IF NOT EXISTS licenses (
  key_hash TEXT PRIMARY KEY,
  max_activations INTEGER NOT NULL DEFAULT 1,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  note TEXT
);

CREATE TABLE IF NOT EXISTS activations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key_hash TEXT NOT NULL,
  device_id TEXT NOT NULL,
  activated_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0,
  UNIQUE(key_hash, device_id),
  FOREIGN KEY(key_hash) REFERENCES licenses(key_hash)
);

CREATE INDEX IF NOT EXISTS idx_activations_key_hash ON activations(key_hash);
CREATE INDEX IF NOT EXISTS idx_activations_device_id ON activations(device_id);
