// ============================================
// METRON WEBAPP — API layer
// ============================================

const BASE_URL = '';

/** Сырой initData из Telegram WebApp (для HMAC-проверки на бэкенде). */
export function getTelegramInitData() {
  return window.Telegram?.WebApp?.initData || '';
}

/** Заголовки авторизации для всех запросов к /api/user/* */
export function buildAuthHeaders(extra = {}) {
  const headers = { ...extra };
  const initData = getTelegramInitData();
  if (initData) {
    headers['X-Telegram-Init-Data'] = initData;
  }
  return headers;
}

async function parseApiError(res) {
  try {
    const body = await res.json();
    if (body?.detail) return String(body.detail);
  } catch {
    // ignore JSON parse failures and fall back to status
  }
  return `API error: ${res.status}`;
}

async function request(path, options = {}) {
  const headers = buildAuthHeaders(options.headers || {});
  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!res.ok) throw new Error(await parseApiError(res));
  return res.json();
}

export async function fetchUserData(userId) {
  const data = await request(`/api/user/${userId}`);
  return {
    id:           data.id,
    username:     data.username || 'Пользователь',
    status:       data.status,
    balance:      data.balance,
    monthly_cost: data.monthly_cost,
    daily_cost:   data.daily_cost,
    expire_at:    data.expire_at,
    days_left:    data.days_left,
    vless_link:   data.vless_link,
    devices:      data.devices ?? [],
  };
}

export async function rotateKey(userId) {
  return request(`/api/user/${userId}/rotate-key`, { method: 'POST' });
}

export async function addDevice(userId, deviceName) {
  return request(`/api/user/${userId}/devices`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_name: deviceName }),
  });
}

export async function rotateDeviceKey(userId, deviceId) {
  return request(`/api/user/${userId}/devices/${deviceId}/rotate`, { method: 'POST' });
}

//Bot name
export async function fetchBotConfig() {
  return request('/api/config');
}
