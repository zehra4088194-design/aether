export interface Env {
  LICENSE_DB: D1Database;
  AETHER_ADMIN_KEY?: string;
}

type JsonBody = Record<string, unknown>;

function json(data: JsonBody, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
}

function normalizeKey(key: string): string {
  return key.trim().replace(/\s+/g, "").toUpperCase();
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function keyHash(licenseKey: string): Promise<string> {
  return sha256Hex(`aether-license-v1:${normalizeKey(licenseKey)}`);
}

function badRequest(message: string): Response {
  return json({ valid: false, message }, { status: 400 });
}

async function readBody(request: Request): Promise<JsonBody> {
  try {
    const body = await request.json();
    return typeof body === "object" && body !== null ? (body as JsonBody) : {};
  } catch {
    return {};
  }
}

async function requireAdmin(request: Request, env: Env): Promise<Response | null> {
  if (!env.AETHER_ADMIN_KEY) {
    return json({ valid: false, message: "Admin key is not configured." }, { status: 503 });
  }
  const provided = request.headers.get("X-Aether-Admin") || "";
  if (provided !== env.AETHER_ADMIN_KEY) {
    return json({ valid: false, message: "Unauthorized." }, { status: 401 });
  }
  return null;
}

async function issueLicense(request: Request, env: Env): Promise<Response> {
  const denied = await requireAdmin(request, env);
  if (denied) return denied;
  const body = await readBody(request);
  const licenseKey = String(body.license_key || body.key || "").trim();
  const maxActivations = Math.max(1, Number(body.max_activations || 1) || 1);
  if (licenseKey.length < 16) return badRequest("License key is required.");
  const hash = await keyHash(licenseKey);
  await env.LICENSE_DB.prepare(
    `INSERT INTO licenses (key_hash, max_activations, active, note)
     VALUES (?1, ?2, 1, ?3)
     ON CONFLICT(key_hash) DO UPDATE SET max_activations = excluded.max_activations, active = 1, note = excluded.note`,
  ).bind(hash, maxActivations, String(body.note || "")).run();
  return json({ valid: true, issued: true, key_hash: hash, max_activations: maxActivations });
}

async function activate(request: Request, env: Env): Promise<Response> {
  const body = await readBody(request);
  const licenseKey = String(body.license_key || "").trim();
  const deviceId = String(body.device_id || "").trim();
  if (licenseKey.length < 16) return badRequest("License key is required.");
  if (!deviceId) return badRequest("Device id is required.");
  const hash = await keyHash(licenseKey);
  const license = await env.LICENSE_DB.prepare(
    "SELECT key_hash, max_activations, active FROM licenses WHERE key_hash = ?1",
  ).bind(hash).first<{ key_hash: string; max_activations: number; active: number }>();
  if (!license || !license.active) {
    return json({ valid: false, message: "This license key is not active." }, { status: 403 });
  }
  const existing = await env.LICENSE_DB.prepare(
    "SELECT id, revoked, device_id FROM activations WHERE key_hash = ?1 AND device_id = ?2 LIMIT 1",
  ).bind(hash, deviceId).first<{ id: number; revoked: number; device_id: string }>();
  const countRow = await env.LICENSE_DB.prepare(
    "SELECT COUNT(*) AS count FROM activations WHERE key_hash = ?1 AND revoked = 0",
  ).bind(hash).first<{ count: number }>();
  const activeCount = Number(countRow?.count || 0);
  if (existing && !existing.revoked) {
    await env.LICENSE_DB.prepare(
      "UPDATE activations SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?1",
    ).bind(existing.id).run();
    return json({ valid: true, activation_id: existing.id, message: "Activation confirmed." });
  }
  if (activeCount >= Number(license.max_activations || 1)) {
    return json({ valid: false, message: "This license key is already active on another PC." }, { status: 403 });
  }
  const inserted = await env.LICENSE_DB.prepare(
    `INSERT INTO activations (key_hash, device_id, activated_at, last_seen_at, revoked)
     VALUES (?1, ?2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)`,
  ).bind(hash, deviceId).run();
  return json({ valid: true, activation_id: inserted.meta.last_row_id, message: "Activation successful." });
}

async function verify(request: Request, env: Env): Promise<Response> {
  const body = await readBody(request);
  const licenseKey = String(body.license_key || "").trim();
  const deviceId = String(body.device_id || "").trim();
  if (licenseKey.length < 16) return badRequest("License key is required.");
  if (!deviceId) return badRequest("Device id is required.");
  const hash = await keyHash(licenseKey);
  const activation = await env.LICENSE_DB.prepare(
    `SELECT a.id, a.revoked, l.active
     FROM activations a
     JOIN licenses l ON l.key_hash = a.key_hash
     WHERE a.key_hash = ?1 AND a.device_id = ?2
     LIMIT 1`,
  ).bind(hash, deviceId).first<{ id: number; revoked: number; active: number }>();
  if (!activation || activation.revoked || !activation.active) {
    return json({ valid: false, message: "This activation is no longer valid." });
  }
  await env.LICENSE_DB.prepare(
    "UPDATE activations SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?1",
  ).bind(activation.id).run();
  return json({ valid: true, message: "Activation verified." });
}

async function deactivate(request: Request, env: Env): Promise<Response> {
  const body = await readBody(request);
  const licenseKey = String(body.license_key || "").trim();
  const deviceId = String(body.device_id || "").trim();
  if (licenseKey.length < 16) return badRequest("License key is required.");
  if (!deviceId) return badRequest("Device id is required.");
  const hash = await keyHash(licenseKey);
  const result = await env.LICENSE_DB.prepare(
    "DELETE FROM activations WHERE key_hash = ?1 AND device_id = ?2",
  ).bind(hash, deviceId).run();
  return json({ deactivated: (result.meta.changes || 0) > 0, message: "Activation removed." });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "aether-license-worker" });
    }
    if (request.method === "POST" && url.pathname === "/v1/admin/issue") {
      return issueLicense(request, env);
    }
    if (request.method === "POST" && url.pathname === "/v1/activate") {
      return activate(request, env);
    }
    if (request.method === "POST" && url.pathname === "/v1/verify") {
      return verify(request, env);
    }
    if (request.method === "POST" && url.pathname === "/v1/deactivate") {
      return deactivate(request, env);
    }
    return json({ message: "Not found." }, { status: 404 });
  },
};
