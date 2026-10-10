/* Optional browser acceptance. Uses isolated runs; never connects to a live Studio. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

async function main() {
  const python = process.env.QUANT_TEST_STUDIO_PYTHON;
  const qdkPython = process.env.QUANT_TEST_INTAKE_PYTHON;
  const qdkSource = process.env.QUANT_TEST_INTAKE_SOURCE;
  const output = process.env.QUANT_BROWSER_ARTIFACTS;
  assert(python && qdkPython && qdkSource && output, 'Set the four documented test paths');
  assert(path.isAbsolute(output), 'Artifact directory must be absolute');
  fs.mkdirSync(output, { recursive: true });
  const root = fs.mkdtempSync(path.join(output, 'isolated-'));
  const config = path.join(root, 'intake-config.json');
  fs.writeFileSync(config, JSON.stringify({ python: qdkPython, source: qdkSource }));
  const env = { ...process.env, PYTHONPATH: path.resolve('src'), PYTHONUTF8: '1',
    QUANT_STUDIO_INTAKE_CONFIG: config };
  const seed = `import json,sys
from pathlib import Path
from quant_studio.runner import run
from quant_studio.experiments import ExperimentStore
root=Path(sys.argv[1]); runs=[]
for i in range(4):
    result=run('synthetic-demo', {'initial_capital':10000+i*1000}, execute=True, runs_root=root)
    assert result.status=='succeeded'
    ExperimentStore(root).annotate(result.run_id, ('=1+2' if i==0 else '合成实验'+str(i)), '', expected='')
    runs.append(result.run_id)
print(json.dumps(runs))`;
  const runs = JSON.parse(execFileSync(python, ['-c', seed, root], { env, encoding: 'utf8', windowsHide: true }));
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const stop = path.join(root, 'stop');
  const script = `import sys
from quant_studio.server import serve
serve(port=int(sys.argv[1]),runs_root=sys.argv[2],stop_file=sys.argv[3])`;
  const child = spawn(python, ['-c', script, String(port), root, stop], {
    env, windowsHide: true,
    stdio: ['ignore', fs.openSync(path.join(root, 'stdout.log'), 'w'), fs.openSync(path.join(root, 'stderr.log'), 'w')],
  });
  let browser;
  const errors = [];
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
    page.on('pageerror', error => errors.push(error.message));
    const base = `http://127.0.0.1:${port}`;
    let ready = false;
    for (let i = 0; i < 60; i++) {
      if (child.exitCode !== null) throw new Error('Preview process stopped');
      try { const response = await page.goto(base + '/intake'); ready = response.status() === 200; }
      catch (_) { await new Promise(resolve => setTimeout(resolve, 250)); }
      if (ready) break;
    }
    assert(ready, 'Preview server did not become ready');
    await page.locator('#data-file').setInputFiles({ name: 'sample.csv', mimeType: 'text/csv',
      buffer: Buffer.from('id,score\n000001,1.25\n000002,2.5\n') });
    await Promise.all([page.waitForURL('**/intake/map/**'), page.getByRole('button', { name: '上传并查看字段' }).click()]);
    await page.locator('[name="type_1"]').selectOption('number');
    await page.locator('[name="unit_1"]').fill('ratio');
    await page.locator('[name="source"]').fill('browser-acceptance');
    await page.locator('[name="provider"]').fill('synthetic');
    await page.locator('[name="primary_key"]').fill('id');
    await page.screenshot({ path: path.join(output, 'mapping-desktop.png'), fullPage: true });
    await Promise.all([page.waitForURL('**/intake/requests/**'), page.locator('form[action="/intake/import"] button').click()]);
    for (let i = 0; i < 45; i++) {
      if (await page.getByRole('link', { name: '查看数据版本与用途检查' }).count()) break;
      await new Promise(resolve => setTimeout(resolve, 500)); await page.reload();
    }
    await page.getByRole('link', { name: '查看数据版本与用途检查' }).click();
    await page.locator('form[action="/intake/check"] button').click();
    await page.locator('[name="project_name"]').fill('浏览器验收项目');
    await page.locator('[name="question"]').fill('这份输入的字段类型与缺失情况如何？');
    await Promise.all([page.waitForURL('**/projects/*'), page.locator('form[action="/intake/connect"] button').click()]);
    assert((await page.locator('h1').innerText()).includes('浏览器验收项目'));
    await page.screenshot({ path: path.join(output, 'project-desktop.png'), fullPage: true });
    for (const route of ['/onboarding', '/batches', '/intake']) {
      const response = await page.goto(base + route); assert.equal(response.status(), 200, route);
    }
    const query = new URLSearchParams(); runs.forEach(run => query.append('run', run));
    await page.goto(base + '/experiments/compare?' + query);
    assert.equal(await page.locator('#curve-svg polyline').count(), 4);
    await page.locator('.curve-choice').nth(3).uncheck();
    assert.equal(await page.locator('#curve-svg polyline').count(), 3);
    await page.locator('#curve-start').fill('1');
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#curve-export').click();
    const download = await downloadPromise;
    const csvPath = path.join(output, 'comparison.csv'); await download.saveAs(csvPath);
    const csv = fs.readFileSync(csvPath, 'utf8');
    assert(csv.includes("'=1+2"), 'Spreadsheet formula prefix must be neutralized');
    assert(csv.includes('-') || csv.includes('0.'), 'Export has data');
    await page.screenshot({ path: path.join(output, 'comparison-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    for (const route of ['/intake', '/onboarding', '/batches']) {
      await page.goto(base + route);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      assert(!overflow, 'Unexpected horizontal page overflow: ' + route);
      await page.screenshot({ path: path.join(output, route.slice(1) + '-mobile.png'), fullPage: true });
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'browser-result.json'), JSON.stringify({ ok: true, root, runs,
      checks: ['upload-map-import-check-project', 'four-curves', 'hide-curve', 'window-export',
        'formula-safe-csv', 'mobile-pages', 'no-browser-errors'] }, null, 2));
    console.log(JSON.stringify({ ok: true, output, root }));
  } finally {
    if (browser) await browser.close();
    fs.writeFileSync(stop, 'stop');
    await Promise.race([new Promise(resolve => child.once('exit', resolve)),
      new Promise(resolve => setTimeout(resolve, 5000))]);
    if (child.exitCode === null) child.kill();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
