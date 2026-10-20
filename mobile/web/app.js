(() => {
  const $ = (id) => document.getElementById(id);
  const settings = $('settings'), status = $('status'), messages = $('messages');
  let endpoint = '';
  let token = '';
  const sayStatus = (text) => { status.textContent = text; };
  const addBubble = (text, kind = '') => {
    const node = document.createElement('div');
    node.className = `bubble ${kind}`.trim();
    node.textContent = text;
    messages.appendChild(node);
    node.scrollIntoView({ behavior: 'smooth', block: 'end' });
    return node;
  };
  const normalizeEndpoint = () => {
    const raw = $('endpoint').value.trim().replace(/\/+$/, '');
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Enter a valid HTTP(S) server URL.');
    if (url.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) throw new Error('Network connections require HTTPS.');
    return url.origin;
  };
  $('settingsToggle').addEventListener('click', () => { settings.hidden = !settings.hidden; });
  $('connect').addEventListener('click', async () => {
    try {
      endpoint = normalizeEndpoint(); token = $('token').value;
      if (token.length < 32) throw new Error('Token must contain at least 32 characters.');
      const response = await fetch(`${endpoint}/api/v1/health`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
      if (!response.ok) throw new Error(response.status === 401 ? 'Authentication failed; check the token.' : `Server returned ${response.status}.`);
      const health = await response.json();
      if (health.ok !== true || health.service !== 'yuki-mobile-api') throw new Error('Unexpected server response.');
      settings.hidden = true; sayStatus('Connected to Yuki. Text-only mobile mode is active.');
    } catch (error) { sayStatus(error instanceof Error ? error.message : 'Connection failed.'); }
  });
  $('chatForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = $('message').value.trim();
    if (!message) return;
    if (!endpoint || !token) { sayStatus('Connect to your Yuki server first.'); settings.hidden = false; return; }
    addBubble(message, 'user'); $('message').value = ''; $('send').disabled = true; sayStatus('Waiting for local inference…');
    try {
      const response = await fetch(`${endpoint}/api/v1/chat`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ message }), cache: 'no-store' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `Server returned ${response.status}.`);
      addBubble(body.answer || 'Yuki returned an empty response.');
      sayStatus('Reply received. No skill was executed.');
    } catch (error) { addBubble(error instanceof Error ? error.message : 'Request failed.', 'error'); sayStatus('Request failed. Check connection and server logs.'); }
    finally { $('send').disabled = false; $('message').focus(); }
  });
})();
