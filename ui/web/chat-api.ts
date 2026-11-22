type YukiRequestOptions = RequestInit & { headers?: HeadersInit };

type YukiErrorPayload = {
  error?: unknown;
};

interface Window {
  YukiApi: {
    request<T>(
      path: string,
      options?: YukiRequestOptions,
      token?: string | null,
      onUnauthorized?: () => void,
    ): Promise<T>;
  };
}

window.YukiApi = {
  async request<T>(
    path: string,
    options: YukiRequestOptions = {},
    token: string | null = null,
    onUnauthorized?: () => void,
  ): Promise<T> {
    const headers = new Headers(options.headers);
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (token) {
      headers.set("Authorization", "Bearer " + token);
    }

    const response = await fetch(path, { ...options, headers });
    const data: unknown = response.status === 204
      ? {}
      : await response.json().catch(() => ({}));

    if (!response.ok) {
      const payload = data as YukiErrorPayload;
      if (response.status === 401) {
        onUnauthorized?.();
      }
      throw new Error(
        typeof payload.error === "string"
          ? payload.error
          : "Request failed (" + response.status + ")",
      );
    }
    return data as T;
  },
};
