// ============================================
// METRON WEBAPP — API layer
// ============================================

const BASE_URL = '';

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, options);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
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
  const res = await fetch(`/api/user/${userId}/rotate-key`, { method: 'POST' });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function addDevice(userId, deviceName) {
  return request(`/api/user/${userId}/devices`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_name: deviceName }),
  });
}
