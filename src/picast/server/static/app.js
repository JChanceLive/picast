/* PiCast Web UI - shared utilities */

// Toast notification
function showToast(msg) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2000);
}

// HTML-escape text
function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// Loading state helper - disables button, shows loading text, restores after promise resolves
function withLoading(btn, loadingText, promise) {
    if (!btn || btn.disabled) return promise;
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = loadingText || '...';
    return promise.finally(() => {
        btn.disabled = false;
        btn.textContent = orig;
    });
}

// Keyboard shortcuts (only on player page)
document.addEventListener('keydown', function(e) {
    // Don't trigger when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    switch(e.key) {
        case ' ':
            e.preventDefault();
            fetch('/api/toggle', {method: 'POST'});
            break;
        case 's':
            fetch('/api/skip', {method: 'POST'});
            break;
    }
});

// Retry a failed queue item
function retryFailed(itemId) {
    return fetch('/api/queue/' + itemId + '/retry', {method: 'POST'})
        .then(r => r.json())
        .then(r => {
            if (r.ok) showToast('Retrying...');
            else showToast(r.error || 'Retry failed');
        })
        .catch(() => showToast('Retry failed'));
}

// Device switcher - redirect to another Pi's web UI
function switchDevice(url) {
    if (url && !url.includes('localhost') && !url.includes('127.0.0.1')) {
        window.location.href = url + window.location.pathname;
    }
}
