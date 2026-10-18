// Cloudflare Worker template for Aether device activation/license verification.
// Deploy this as a Worker and set:
//   - LICENSES: JSON object/secret map of valid keys (or use D1/KV instead)
//   - ADMIN_TOKEN: secret bearer token used only by your admin tool
//   - AETHER_SERVER_TOKEN: optional shared secret if you want extra auth
//
// Expected app calls:
//   POST /v1/activate  { license_key, device_id }
//   POST /v1/verify    { license_key, device_id }
//   POST /v1/deactivate { license_key, device_id }
//
// Aether desktop client matches this contract already via aether/license_manager.py.

const HEALTH = { ok: true, service: 'aether-license', version: '0.1.0' };

function jsonResponse(status, body, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': 'application/json',
      'access-control-allow-origin': '*',
      'access-control-allow-methods': 'POST, OPTIONS',
      'access-control-allow-headers': 'content-type, x-aether-admin, authorization',
      ...extra,
    },
  });
}

function normalizeLicenseKey(raw) {
  return String(raw || '').trim().toUpperCase();
}

function getLicenseStore(env) {
  if (env.LICENSES && typeof env.LICENSES === 'object') return env.LICENSES;
  if (typeof env.LICENSES === 'string') {
    try {
      return JSON.parse(env.LICENSES);
    } catch {
      return {};
    }
  }
  return {};
}

function authorize(req, env) {
  const adminToken = req.headers.get('x-aether-admin') || req.headers.get('authorization') || '';
  const expected = env.ADMIN_TOKEN || env.AETHER_SERVER_TOKEN || '';
  if (!expected) return true;
  if (adminToken.toLowerCase().startsWith('bearer ')) {
    return adminToken.slice(7) === expected;
  }
  return adminToken === expected;
}

async function parseBody(req) {
  try {
    return await req.json();
  } catch {
    return {};
  }
}

function createSeatRecord(key, deviceId) {
  return {
    key,
    device_id: deviceId,
    valid: true,
    activated_at: new Date().toISOString(),
    revoked: false,
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      return jsonResponse(204, null, { 'access-control-max-age': '86400' });
    }

    if (url.pathname === '/health') {
      return jsonResponse(200, HEALTH);
    }

    if (!['/v1/activate', '/v1/verify', '/v1/deactivate'].includes(url.pathname)) {
      return jsonResponse(404, { message: 'Not found.' });
    }

    if (!authorize(request, env)) {
      return jsonResponse(401, { message: 'Unauthorized.' });
    }

    const payload = await parseBody(request);
    const licenseKey = normalizeLicenseKey(payload.license_key);
    const deviceId = String(payload.device_id || '').trim();

    if (!licenseKey || !deviceId) {
      return jsonResponse(400, { message: 'license_key and device_id are required.' });
    }

    const store = getLicenseStore(env);
    const entry = store[licenseKey] || null;

    if (url.pathname === '/v1/activate') {
      if (!entry || !entry.active) {
        return jsonResponse(422, { valid: false, message: 'This license key is not valid.' });
      }
      const alreadyBound = entry.device_id && entry.device_id !== deviceId;
      if (alreadyBound) {
        return jsonResponse(409, { valid: false, message: 'This key is already activated on another device.' });
      }
      const activation = createSeatRecord(licenseKey, deviceId);
      // In production, store this in KV or D1 instead of in memory.
      env.ACTIVATIONS?.put?.(deviceId, JSON.stringify({ ...activation, license_key: licenseKey }));
      return jsonResponse(200, {
        valid: true,
        activation_id: `activation-${deviceId.slice(0, 12)}`,
        message: 'Activation accepted.',
      });
    }

    if (url.pathname === '/v1/verify') {
      const activation = await env.ACTIVATIONS?.get?.(deviceId);
      const activeRecord = activation ? JSON.parse(activation) : null;
      if (!entry || !entry.active) {
        return jsonResponse(200, { valid: false, message: 'License disabled or expired.' });
      }
      if (activeRecord && activeRecord.device_id === deviceId && activeRecord.license_key === licenseKey) {
        return jsonResponse(200, { valid: true, message: 'Activation confirmed.' });
      }
      return jsonResponse(200, { valid: false, message: 'This activation is not valid for this device.' });
    }

    if (url.pathname === '/v1/deactivate') {
      if (entry && entry.device_id && entry.device_id !== deviceId) {
        return jsonResponse(200, { deactivated: false, message: 'This key is bound to another device.' });
      }
      await env.ACTIVATIONS?.delete?.(deviceId);
      return jsonResponse(200, { deactivated: true, message: 'Seat released.' });
    }

    return jsonResponse(500, { message: 'Unexpected route.' });
  },
};

/*
Example LICENSES map for the admin tool:
{
  "AETH-1111-2222-3333-4444": {
    "active": true,
    "device_id": "",
    "plan": "pro",
    "expires_at": "2030-12-31T23:59:59Z"
  }
}
*/
