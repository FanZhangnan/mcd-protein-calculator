// Shared helper: wait until a Chrome DevTools endpoint answers.
// Chrome 155 sometimes needs more than the old fixed 2.5 s.
export async function waitForJson(url, options = {}, timeout = 25000) {
  const started = Date.now();
  let last = null;
  while (Date.now() - started < timeout) {
    try {
      const resp = await fetch(url, options);
      if (resp.ok) return await resp.json();
      last = new Error(`HTTP ${resp.status}`);
    } catch (err) {
      last = err;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(`CDP endpoint not ready: ${url} (${last})`);
}
