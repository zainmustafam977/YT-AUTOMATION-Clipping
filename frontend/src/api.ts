// ─── API Client ─────────────────────────────────────────────────────────────
// Thin wrapper around fetch that adds CSRF header and handles 401 redirects.

const CSRF_META = document.querySelector('meta[name="csrf-token"]');
const CSRF_TOKEN = CSRF_META ? (CSRF_META as HTMLMetaElement).content : '';

export async function apiFetch(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };
  if (CSRF_TOKEN) headers['X-CSRF-Token'] = CSRF_TOKEN;

  const response = await fetch(path, { ...options, headers });

  if (response.status === 401) {
    window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
    return response;
  }
  return response;
}

export async function apiGet<T = unknown>(path: string): Promise<T> {
  const r = await apiFetch(path);
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json() as Promise<T>;
}

export async function apiPost<T = unknown>(
  path: string,
  body?: unknown
): Promise<T> {
  const r = await apiFetch(path, {
    method: 'POST',
    body: body != null ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(err || `POST ${path} → ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export async function apiPatch<T = unknown>(
  path: string,
  body?: unknown
): Promise<T> {
  const r = await apiFetch(path, {
    method: 'PATCH',
    body: body != null ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(err || `PATCH ${path} → ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export async function apiDelete(path: string): Promise<void> {
  const r = await apiFetch(path, { method: 'DELETE' });
  if (!r.ok) throw new Error(`DELETE ${path} → ${r.status}`);
}

export async function apiUpload<T = unknown>(
  path: string,
  formData: FormData
): Promise<T> {
  const headers: Record<string, string> = {};
  if (CSRF_TOKEN) headers['X-CSRF-Token'] = CSRF_TOKEN;

  const response = await fetch(path, {
    method: 'POST',
    body: formData,
    headers,
  });

  if (response.status === 401) {
    window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
    throw new Error('Unauthorized');
  }
  if (!response.ok) {
    const err = await response.text();
    throw new Error(err || `POST ${path} → ${response.status}`);
  }
  return response.json() as Promise<T>;
}
