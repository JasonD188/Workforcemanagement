

let _csrfToken = null;
let _csrfTokenPromise = null;


async function getCsrfToken() {
  if (_csrfToken) return _csrfToken;
  if (_csrfTokenPromise) return _csrfTokenPromise;

  _csrfTokenPromise = fetch('/api/csrf-token', { credentials: 'same-origin' })
    .then((res) => {
      if (!res.ok) throw new Error('Failed to fetch CSRF token');
      return res.json();
    })
    .then((data) => {
      _csrfToken = data.csrf_token;
      _csrfTokenPromise = null;
      return _csrfToken;
    })
    .catch((err) => {
      _csrfTokenPromise = null;
      throw err;
    });

  return _csrfTokenPromise;
}

function resetCsrfToken() {
  _csrfToken = null;
  _csrfTokenPromise = null;
}


async function secureFetch(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const needsToken = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);

  const headers = { ...(options.headers || {}) };

  if (needsToken) {
    try {
      const token = await getCsrfToken();
      headers['X-CSRFToken'] = token;
    } catch (err) {
      console.error('CSRF token fetch failed:', err);
   
    }
  }

  return fetch(url, { ...options, headers, credentials: 'same-origin' });
}