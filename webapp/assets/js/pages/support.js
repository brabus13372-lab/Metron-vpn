    const tg = window.Telegram?.WebApp;
    if (tg) { tg.expand(); tg.ready(); }

    // Telegram initData имеет приоритет; ?uid=123 — fallback для dev/local
    const _tgId  = tg?.initDataUnsafe?.user?.id ?? null;
    const _params = new URLSearchParams(window.location.search);
    const _uidParam = _params.get('uid') ? parseInt(_params.get('uid'), 10) : null;
    const USER_ID = _tgId ?? _uidParam;

    let lastSentAt = 0;
    const COOLDOWN_MS = 10_000;

    // Файлы: храним как { file, name, size }
    let attachedFiles = [];
    const MAX_FILES    = 5;
    const MAX_SIZE_MB  = 10;

    // ---- Textarea ----
    function onTextInput() {
      const ta  = document.getElementById('support-text');
      const cnt = document.getElementById('char-counter');
      cnt.textContent = `${ta.value.length} / 1000`;
      cnt.className   = 'char-counter' + (ta.value.length > 900 ? ' warn' : '');
      updateSendBtn();
    }

    function updateSendBtn() {
      const ta  = document.getElementById('support-text');
      const btn = document.getElementById('btn-send');
      btn.disabled = !USER_ID || ta.value.trim().length < 5;
    }

    // ---- Файлы ----
    function onFilesSelected(input) {
      const files = Array.from(input.files);
      for (const file of files) {
        if (attachedFiles.length >= MAX_FILES) {
          showToast(`⚠️ Максимум ${MAX_FILES} файлов`);
          break;
        }
        if (file.size > MAX_SIZE_MB * 1024 * 1024) {
          showToast(`⚠️ ${file.name} слишком большой (макс ${MAX_SIZE_MB} МБ)`);
          continue;
        }
        if (attachedFiles.find(f => f.name === file.name)) continue;
        attachedFiles.push(file);
      }
      input.value = '';
      renderFileChips();
    }

    function removeFile(name) {
      attachedFiles = attachedFiles.filter(f => f.name !== name);
      renderFileChips();
    }

    function renderFileChips() {
      const area = document.getElementById('attach-area');
      area.querySelectorAll('.file-chip').forEach(el => el.remove());
      for (const file of attachedFiles) {
        const chip = document.createElement('div');
        chip.className = 'file-chip';
        chip.innerHTML = `
          <span>${escHtml(file.name)}</span>
          <span class="file-chip-remove" onclick="removeFile('${escHtml(file.name)}')" title="Удалить">×</span>
        `;
        area.appendChild(chip);
      }
    }

    // ---- Отправка ----
    async function sendTicket() {
      const ta   = document.getElementById('support-text');
      const text = ta.value.trim();

      if (!USER_ID)        { showToast('⚠️ Откройте через Telegram'); return; }
      if (text.length < 5) { showToast('⚠️ Слишком короткое сообщение'); return; }

      const now = Date.now();
      if (now - lastSentAt < COOLDOWN_MS) {
        const sec = Math.ceil((COOLDOWN_MS - (now - lastSentAt)) / 1000);
        showToast(`⏳ Подождите ещё ${sec} сек`);
        return;
      }

      const btn = document.getElementById('btn-send');
      btn.disabled = true;
      btn.innerHTML = '<div class="spinner"></div> Отправляем...';

      try {
        const form = new FormData();
        form.append('message', text);
        for (const file of attachedFiles) {
          form.append('files', file, file.name);
        }

        const res = await fetch(`/api/user/${USER_ID}/support`, {
          method: 'POST',
          body: form,
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.detail || `HTTP ${res.status}`);
        }

        lastSentAt    = Date.now();
        ta.value      = '';
        attachedFiles = [];
        renderFileChips();
        onTextInput();
        showToast('✅ Обращение отправлено — ждите ответа в боте');
        await loadTickets();
      } catch (e) {
        const msg = e?.message ? `❌ ${e.message}` : '❌ Ошибка отправки, попробуйте позже';
        showToast(msg);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Отправить';
      }
    }

    // ---- История ----
    async function loadTickets() {
      if (!USER_ID) {
        renderTickets(null);
        showToast('⚠️ Откройте приложение через Telegram');
        return;
      }
      try {
        const res = await fetch(`/api/user/${USER_ID}/support`);
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        renderTickets(data.tickets ?? []);
      } catch {
        renderTickets(null);
      }
    }

    function renderTickets(tickets) {
      const list = document.getElementById('tickets-list');
      if (tickets === null) {
        list.innerHTML = `<div style="padding:var(--space-4) 0;text-align:center;">
          <span style="font-size:var(--text-sm);color:var(--color-text-faint);">Ошибка загрузки</span></div>`;
        return;
      }
      if (!tickets.length) {
        list.innerHTML = `<div style="padding:var(--space-4) 0;text-align:center;">
          <span style="font-size:var(--text-sm);color:var(--color-text-faint);font-style:italic;">Обращений пока нет</span></div>`;
        return;
      }
      list.innerHTML = tickets.map(t => {
        const date = new Date(t.created_at).toLocaleString('ru-RU', {
          day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
        });
        const statusMap = {
          answered: '<span class="ticket-status answered">✅ Отвечено</span>',
          closed: '<span class="ticket-status answered">📦 Закрыто</span>',
          pending: '<span class="ticket-status pending">⏳ Ожидает</span>',
        };
        const statusHtml = statusMap[t.status] || statusMap.pending;
        const filesHtml = t.files?.length
          ? `<span style="font-size:var(--text-xs);color:var(--color-text-faint);">📎 ${t.files.length} файл(а)</span>`
          : '';
        return `<div class="ticket-item">
          <div class="ticket-text">${escHtml(t.message)}</div>
          <div style="display:flex;justify-content:space-between;align-items:center;gap:var(--space-2);">
            <span class="ticket-meta">${date} ${filesHtml}</span>
            ${statusHtml}
          </div>
        </div>`;
      }).join('');
    }

    function escHtml(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function showToast(msg) {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.style.display = 'block';
      t.style.animation = 'none';
      t.offsetHeight;
      t.style.animation = '';
      clearTimeout(t._timer);
      t._timer = setTimeout(() => { t.style.display = 'none'; }, 2500);
    }

    loadTickets();
