// ============================================
// METRON WEBAPP — API layer
// ============================================

const BASE_URL = '';

async function request(path) {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchUserData(userId) {
  const data = await request(`/api/user/${userId}`);

  const expireDate = new Date(data.expire_at);
  const now = new Date();
  const daysLeft = Math.max(0, Math.ceil((expireDate - now) / (1000 * 60 * 60 * 24)));

  return {
    name:             data.username || 'Пользователь',
    plan:             data.status === 'ACTIVE' ? 'Premium' : data.status === 'TRIAL' ? 'Пробный' : 'Неактивен',
    subscription_end: expireDate.toLocaleDateString('ru-RU'),
    days_left:        daysLeft,
    days_total:       30,
    balance:          data.balance,
    vless_key:        data.vless_link,
  };
}

export async function rotateKey(userId) {
  const res = await fetch(`/api/user/${userId}/rotate`, { method: 'POST' });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}