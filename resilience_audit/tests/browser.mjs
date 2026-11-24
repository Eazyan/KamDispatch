/* Requires a fresh Chromium profile on localhost:19271 with external network blocked. */
import { writeFile } from 'node:fs/promises';

const debug = 'http://127.0.0.1:19271';
const target = await (await fetch(debug + '/json/new?about:blank', { method: 'PUT' })).json();
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});
let sequence = 0;
const pending = new Map();
const failed = new Set();
const requests = new Map();
socket.addEventListener('message', ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  }
  if (message.method === 'Network.requestWillBeSent') {
    requests.set(message.params.requestId, message.params.request.url);
  }
  if (message.method === 'Network.loadingFailed' && !message.params.canceled) {
    const requested = requests.get(message.params.requestId);
    if (requested) {
      const url = new URL(requested);
      failed.add(url.origin + url.pathname);
    }
  }
});
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
function evaluate(expression) {
  return call('Runtime.evaluate', { expression, returnByValue: true }).then(result => result.result.value);
}

await call('Page.enable');
await call('Network.enable');
await call('Network.setBlockedURLs', { urls: ['https://*'] });
await call('Emulation.setDeviceMetricsOverride', { width: 1360, height: 900, deviceScaleFactor: 1, mobile: false });
const results = {};
try {
  const report = process.argv.includes('--report');
  const standalone = process.argv.includes('--standalone');
  const origin = report ? '' : standalone ? 'http://127.0.0.1:18089' : 'http://127.0.0.1:18088';
  const pages = report ? { report: new URL('../index.html', import.meta.url).href }
    : standalone ? { standalone: '/' } : { dashboard: '/', city: '/city/', map: '/static/ops/index.html' };
  for (const [name, path] of Object.entries(pages)) {
    failed.clear();
    await call('Page.navigate', { url: origin + path });
    for (let attempt = 0; attempt < 35; attempt++) {
      const ready = await evaluate(`location.href === ${JSON.stringify(origin + path)}
        && document.readyState === 'complete' && document.body?.innerText.length > 80`);
      if (ready) break;
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    await new Promise(resolve => setTimeout(resolve, 1700));
    const state = await evaluate(`({
      title: document.title,
      canvas_count: document.querySelectorAll('canvas').length,
      visible_text: document.body.innerText.split('\\n').map(s => s.trim()).filter(Boolean).slice(0, 22),
    })`);
    results[name] = { ...state, failed_requests: [...failed].sort() };
    if (report) {
      const interaction = await evaluate(`(() => {
        const buttons = document.querySelectorAll('nav button');
        buttons[5]?.click();
        return { buttons: buttons.length, detail: document.querySelector('#detail').innerText,
          highlighted: document.querySelectorAll('.node.affected').length };
      })()`);
      if (interaction.buttons !== 10 || !interaction.detail.includes('F06 · FAIL') || interaction.highlighted < 2) {
        throw new Error('Unexpected report interaction');
      }
      results[name].interaction = interaction;
    }
    const screenshot = await call('Page.captureScreenshot', { format: 'png', fromSurface: true });
    await writeFile(new URL(`../evidence/${name}.png`, import.meta.url), Buffer.from(screenshot.data, 'base64'));
  }
} finally {
  socket.close();
  await fetch(debug + '/json/close/' + target.id);
}
await writeFile(new URL(`../evidence/browser-${Object.keys(results)[0]}.json`, import.meta.url),
  JSON.stringify(results, null, 2) + '\n');
