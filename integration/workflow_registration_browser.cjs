/* Current-date synthetic fixture only. Never accepts a running production URL. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

async function main() {
  const fixturePath = process.env.QUANT_WORKFLOW_FIXTURE;
  const output = process.env.QUANT_BROWSER_ARTIFACTS;
  assert(fixturePath && output && path.isAbsolute(output));
  const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  assert(fixture.scope.startsWith('SYNTHETIC software acceptance only'));
  fs.mkdirSync(output, { recursive: true });
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const stop = path.join(output, 'stop');
  assert(!fs.existsSync(stop), 'Use a new browser artifact directory');
  const env = { ...process.env, ...fixture.environment, PYTHONDONTWRITEBYTECODE: '1' };
  const child = spawn(fixture.python, ['-B', '-X', 'utf8', '-m', 'quant_studio', 'serve',
    '--host', '127.0.0.1', '--port', String(port), '--runs-root', fixture.runs_root, '--stop-file', stop], {
    env, windowsHide: true, stdio: ['ignore',
      fs.openSync(path.join(output, 'stdout.log'), 'w'),
      fs.openSync(path.join(output, 'stderr.log'), 'w')],
  });
  let browser;
  const errors = [];
  try {
    browser = await chromium.launch({ headless: true,
      ...(process.env.QUANT_BROWSER_CHANNEL ? { channel: process.env.QUANT_BROWSER_CHANNEL } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => errors.push(error.message));
    const base = `http://127.0.0.1:${port}`;
    let ready = false;
    for (let i = 0; i < 80; i++) {
      if (child.exitCode !== null) throw new Error('Isolated preview server stopped');
      try { ready = (await page.goto(base + '/today')).status() === 200; }
      catch (_) { await new Promise(resolve => setTimeout(resolve, 250)); }
      if (ready) break;
    }
    assert(ready);
    assert((await page.locator('body').innerText()).includes('四项状态分别核对'));
    await page.screenshot({ path: path.join(output, 'today.png'), fullPage: true });
    await page.goto(base + '/experiments/compare?run=baseline-a&run=baseline-b');
    assert((await page.locator('body').innerText()).includes('冻结输入身份、代码身份及执行/检验协议一致'));
    await page.screenshot({ path: path.join(output, 'comparable.png'), fullPage: true });
    await page.goto(base + '/experiments/compare?run=baseline-a&run=changed&run=failed');
    assert((await page.locator('body').innerText()).includes('未合并排名'));
    assert((await page.locator('body').innerText()).includes('状态：失败'));
    await page.screenshot({ path: path.join(output, 'incomparable.png'), fullPage: true });
    await page.goto(base + fixture.project_url);
    const decision = page.locator(`form[action="${fixture.project_url}/decision"]`);
    await decision.locator('[name="verdict"]').selectOption('inconclusive');
    await decision.locator('[name="action"]').selectOption('nominate_paper');
    await decision.locator('[name="candidate"]').selectOption('base');
    await decision.locator('[name="rationale"]').fill('SYNTHETIC 浏览器软件验收；不评价策略有效性，不计自然交易日。');
    await decision.locator('[name="evidence"][value="baseline-a"]').check();
    await Promise.all([page.waitForURL('**/projects/*#research-card'), decision.getByRole('button').click()]);
    await page.goto(base + fixture.registration_url);
    const preview = page.locator(`form[action="${fixture.project_url}/registration-preview"]`);
    const choice = await preview.locator('option').evaluateAll(options => options.find(o => o.textContent.includes('baseline-a') && o.textContent.endsWith('base')).value);
    await preview.locator('[name="selected"]').selectOption(choice);
    await preview.locator('[name="start"]').fill(fixture.start);
    await preview.locator('[name="end"]').fill(fixture.end);
    await Promise.all([page.waitForURL('**/registration?preview=*'), preview.getByRole('button').click()]);
    const previewURL = page.url();
    assert((await page.locator('body').innerText()).includes('初始模拟资金'));
    await page.screenshot({ path: path.join(output, 'preview.png'), fullPage: true });
    const confirmation = page.locator(`form[action="${fixture.project_url}/registration-confirm"]`);
    await confirmation.locator('[name="confirmed"]').check();
    await Promise.all([page.waitForURL('**/jobs/*'), confirmation.getByRole('button').click()]);
    const jobURL = page.url();
    let completed = false;
    for (let i = 0; i < 90; i++) {
      await page.goto(previewURL);
      const text = await page.locator('body').innerText();
      if (text.includes('当前账户独立核验')) { completed = true; break; }
      if (text.includes('登记失败，可核验后恢复')) throw new Error(text);
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    assert(completed, `Registration did not complete: ${jobURL}`);
    assert((await page.locator('body').innerText()).includes('尚未接管任何每日调度'));
    await page.screenshot({ path: path.join(output, 'registered-account.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    for (const [name, route] of [['registration-mobile', previewURL], ['project-mobile', base + fixture.project_url], ['today-mobile', base + '/today']]) {
      await page.goto(route);
      assert(!(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)), `Page overflow: ${name}`);
      await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'browser-result.json'), JSON.stringify({
      status: 'passed', scope: fixture.scope, port, project: fixture.project_url,
      previewURL, jobURL, checks: ['today', 'comparable', 'incomparable', 'decision',
        'preview', 'explicit-confirmation', 'native-registration', 'independent-inspection', 'mobile'],
    }, null, 2));
  } finally {
    if (browser) await browser.close();
    fs.writeFileSync(stop, 'isolated browser acceptance finished');
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve();
      const timer = setTimeout(() => { child.kill(); resolve(); }, 10000);
      child.once('exit', () => { clearTimeout(timer); resolve(); });
    });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
