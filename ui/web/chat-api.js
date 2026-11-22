window.YukiApi = {
  async request(path, options = {}, token = null, onUnauthorized) {
    const headers = new Headers(options.headers);
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (token) {
      headers.set("Authorization", "Bearer " + token);
    }

    const response = await fetch(path, { ...options, headers });
    const data = response.status === 204
      ? {}
      : await response.json().catch(() => ({}));

    if (!response.ok) {
      if (response.status === 401 && typeof onUnauthorized === "function") {
        onUnauthorized();
      }
      const payload = data;
      const message = payload && typeof payload.error === "string"
        ? payload.error
        : "Request failed (" + response.status + ")";
      throw new Error(message);
    }
    return data;
  },
};
