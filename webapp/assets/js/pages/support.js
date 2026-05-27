    import { buildAuthHeaders } from '../api.js';

    const tg = window.Telegram?.WebApp;
    if (tg) { tg.expand(); tg.ready(); }

    const USER_ID = tg?.initDataUnsafe?.user?.id ?? null;

    let lastSentAt = 0;
    const COOLDOWN_MS = 10_000;

    let attachedFiles = [];
    const MAX_FILES = 5;
    const MAX_SIZE_MB = 10;
    let _isSending = false;
    let _ticketsLoadSeq = 0;
    let _ticketsLoaded = false;
    let _lastVisibleRefreshAt = 0;

    async function parseApiError(res) {
      try {
        const body = await res.json();
        if (body?.detail) return String(body.detail);
      } catch {
        // ignore parse failures
      }
      return `API error: ${res.status}`;
    }

    function formatFileSize(bytes) {
      if (!Number.isFinite(bytes) || bytes <= 0) return '0 КБ';
      if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
    }

    function autoResizeTextarea() {
      const ta = document.getElementById('support-text');
      if (!ta) return;
      ta.style.height = 'auto';
      ta.style.height = `${Math.min(220, ta.scrollHeight)}px`;
    }

    // ---- Textarea ----
    function onTextInput() {
      const ta  = document.getElementById('support-text');
      const cnt = document.getElementById('char-counter');
      cnt.textContent = `${ta.value.length} / 1000`;
      cnt.className   = 'char-counter' + (ta.value.length > 900 ? ' warn' : '');
      autoResizeTextarea();
      updateSendBtn();
    }

    function updateSendBtn() {
      const ta  = document.getElementById('support-text');
      const btn = document.getElementById('btn-send');
      btn.disabled = _isSending || !USER_ID || ta.value.trim().length < 5;
    }

    function renderSupportState(title, description, allowRetry = false) {
      const list = document.getElementById('tickets-list');
      list.innerHTML = `
        <div class="support-empty-state">
          <div class="support-empty-title">${escHtml(title)}</div>
          <div class="support-empty-desc">${escHtml(description)}</div>
          ${allowRetry ? '<button class="btn btn-ghost support-refresh-btn" style="margin-top:var(--space-4);" onclick="refreshTickets()" type="button">Повторить</button>' : ''}
        </div>`;
    }

    function buildTicketsLoadingMarkup() {
      return `
        <div class="ticket-item" aria-hidden="true">
          <div class="ticket-header">
            <span class="ticket-status pending">Загрузка</span>
          </div>
          <div class="ticket-text skeleton">Загружаем историю обращений</div>
          <div class="ticket-meta-row">
            <span class="ticket-meta skeleton">00.00 00:00</span>
          </div>
        </div>
        <div class="ticket-item" aria-hidden="true">
          <div class="ticket-header">
            <span class="ticket-status pending">Загрузка</span>
          </div>
          <div class="ticket-text skeleton">Загружаем историю обращений</div>
          <div class="ticket-meta-row">
            <span class="ticket-meta skeleton">00.00 00:00</span>
          </div>
        </div>`;
    }

    function setTicketsLoading(isLoading, { soft = false } = {}) {
      const list = document.getElementById('tickets-list');
      const refreshBtn = document.getElementById('btn-refresh-tickets');
      list.classList.toggle('is-soft-loading', Boolean(isLoading && soft));
      if (refreshBtn) refreshBtn.disabled = isLoading;
      if (isLoading && (!soft || !_ticketsLoaded)) {
        list.innerHTML = buildTicketsLoadingMarkup();
      }
    }

    // ---- Файлы ----
    function onFilesSelected(input) {
      const files = Array.from(input.files || []);
      for (const file of files) {
        if (attachedFiles.length >= MAX_FILES) {
          showToast(`⚠️ Максимум ${MAX_FILES} файлов`, 'error');
          break;
        }
        if (file.size > MAX_SIZE_MB * 1024 * 1024) {
          showToast(`⚠️ ${file.name} слишком большой (макс ${MAX_SIZE_MB} МБ)`, 'error');
          continue;
        }
        if (attachedFiles.find(f => f.name === file.name && f.size === file.size)) {
          continue;
        }
        attachedFiles.push(file);
      }
      input.value = '';
      renderFileChips();
    }

    function removeFile(index) {
      attachedFiles = attachedFiles.filter((_, i) => i !== index);
      renderFileChips();
    }

    function renderFileChips() {
      const area = document.getElementById('attach-area');
      area.querySelectorAll('.file-chip').forEach(el => el.remove());

      attachedFiles.forEach((file, index) => {
        const chip = document.createElement('div');
        chip.className = 'file-chip';

        const name = document.createElement('span');
        name.textContent = file.name;

        const meta = document.createElement('span');
        meta.className = 'file-chip-meta';
        meta.textContent = formatFileSize(file.size);

        const removeBtn = document.createElement('button');
        removeBtn.className = 'file-chip-remove';
        removeBtn.type = 'button';
        removeBtn.title = 'Удалить';
        removeBtn.textContent = '×';
        removeBtn.addEventListener('click', () => removeFile(index));

        chip.appendChild(name);
        chip.appendChild(meta);
        chip.appendChild(removeBtn);
        area.appendChild(chip);
      });
    }

    // ---- Отправка ----
    async function sendTicket() {
      if (_isSending) return;

      const ta   = document.getElementById('support-text');
      const text = ta.value.trim();

      if (!USER_ID)        { showToast('⚠️ Откройте приложение через Telegram', 'error'); return; }
      if (text.length < 5) { showToast('⚠️ Слишком короткое сообщение', 'error'); return; }

      const now = Date.now();
      if (now - lastSentAt < COOLDOWN_MS) {
        const sec = Math.ceil((COOLDOWN_MS - (now - lastSentAt)) / 1000);
        showToast(`⏳ Подождите ещё ${sec} сек`, 'error');
        return;
      }

      const btn = document.getElementById('btn-send');
      _isSending = true;
      updateSendBtn();
      btn.innerHTML = '<div class="spinner"></div> Отправляем...';

      try {
        const form = new FormData();
        form.append('message', text);
        for (const file of attachedFiles) {
          form.append('files', file, file.name);
        }

        const res = await fetch(`/api/user/${USER_ID}/support`, {
          method: 'POST',
          headers: buildAuthHeaders(),
          body: form,
        });
        if (!res.ok) {
          throw new Error(await parseApiError(res));
        }

        lastSentAt = Date.now();
        ta.value = '';
        attachedFiles = [];
        renderFileChips();
        onTextInput();
        ta.focus();
        showToast('✅ Обращение отправлено — ждите ответа в боте');
        await loadTickets({ soft: true });
      } catch (e) {
        const msg = e?.message || 'Ошибка отправки, попробуйте позже';
        showToast(`❌ ${msg}`, 'error');
      } finally {
        _isSending = false;
        btn.textContent = 'Отправить';
        updateSendBtn();
      }
    }

    // ---- История ----
    async function loadTickets({ soft = false } = {}) {
      if (!USER_ID) {
        renderSupportState(
          'Поддержка недоступна',
          'Откройте приложение через Telegram, чтобы отправлять обращения и видеть историю.'
        );
        updateSendBtn();
        return;
      }

      const seq = ++_ticketsLoadSeq;
      setTicketsLoading(true, { soft: soft && _ticketsLoaded });

      try {
        const res = await fetch(`/api/user/${USER_ID}/support`, {
          headers: buildAuthHeaders(),
        });
        if (!res.ok) throw new Error(await parseApiError(res));
        const data = await res.json();
        if (seq !== _ticketsLoadSeq) return;
        renderTickets(data.tickets ?? []);
        _ticketsLoaded = true;
      } catch (e) {
        if (seq !== _ticketsLoadSeq) return;
        const msg = e?.message || 'Ошибка загрузки';
        renderSupportState('История не загрузилась', msg, true);
      } finally {
        if (seq === _ticketsLoadSeq) {
          setTicketsLoading(false, { soft: soft && _ticketsLoaded });
        }
      }
    }

    function renderTickets(tickets) {
      const list = document.getElementById('tickets-list');
      if (!tickets.length) {
        renderSupportState(
          'Обращений пока нет',
          'Когда вы отправите первое сообщение в поддержку, оно появится здесь.'
        );
        return;
      }

      list.innerHTML = tickets.map(t => {
        const createdAt = new Date(t.created_at).toLocaleString('ru-RU', {
          day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
        });
        const answeredAt = t.answered_at
          ? new Date(t.answered_at).toLocaleString('ru-RU', {
              day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
            })
          : null;

        const statusMap = {
          answered: { cls: 'answered', label: '✅ Отвечено' },
          closed: { cls: 'closed', label: '📦 Закрыто' },
          pending: { cls: 'pending', label: '⏳ Ожидает' },
        };
        const status = statusMap[t.status] || statusMap.pending;
        const filesHtml = t.files?.length
          ? `<span class="ticket-files">📎 ${t.files.length} файл(а)</span>`
          : '';
        const answerMeta = answeredAt
          ? `<span class="ticket-files">Ответ: ${answeredAt}</span>`
          : '';

        return `
          <div class="ticket-item">
            <div class="ticket-header">
              <span class="ticket-status ${status.cls}">${status.label}</span>
            </div>
            <div class="ticket-text">${escHtml(t.message)}</div>
            <div class="ticket-meta-row">
              <span class="ticket-meta">
                <span>${createdAt}</span>
                ${filesHtml}
                ${answerMeta}
              </span>
            </div>
          </div>`;
      }).join('');
    }

    function escHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;')
        .replace(/</g,'&lt;')
        .replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;')
        .replace(/'/g, '&#39;');
    }

    function showToast(msg, type = 'default') {
      const toast = document.getElementById('toast');
      toast.className = 'toast' + (type === 'error' ? ' toast-error' : '');
      toast.innerHTML = '';

      const inner = document.createElement('div');
      inner.className = 'toast-inner';
      inner.textContent = msg;

      if (type === 'error') {
        const closeBtn = document.createElement('button');
        closeBtn.className = 'toast-close';
        closeBtn.setAttribute('aria-label', 'Закрыть');
        closeBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
        closeBtn.onclick = hideToast;
        inner.appendChild(closeBtn);
      }

      toast.appendChild(inner);
      toast.style.display = 'block';
      toast.classList.remove('toast-hiding');
      clearTimeout(toast._timer);
      toast._timer = setTimeout(() => hideToast(), type === 'error' ? 8000 : 2500);
    }

    function hideToast() {
      const toast = document.getElementById('toast');
      clearTimeout(toast._timer);
      if (toast.style.display === 'none') return;
      toast.classList.add('toast-hiding');
      setTimeout(() => {
        toast.style.display = 'none';
        toast.className = 'toast';
        toast.innerHTML = '';
        toast.classList.remove('toast-hiding');
      }, 220);
    }

    function refreshTickets() {
      loadTickets({ soft: _ticketsLoaded });
    }

    onTextInput();
    renderFileChips();
    loadTickets();

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      const now = Date.now();
      if (now - _lastVisibleRefreshAt < 5000) return;
      _lastVisibleRefreshAt = now;
      loadTickets({ soft: true });
    });

    window.sendTicket = sendTicket;
    window.refreshTickets = refreshTickets;
