/* Optional local acceptance; uses isolated synthetic data and real Notebook kernels. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

async function main() {
  const python = process.env.QUANT_TEST_STUDIO_PYTHON;
  const notebookPython = process.env.QUANT_TEST_NOTEBOOK_PYTHON;
  const output = process.env.QUANT_BROWSER_ARTIFACTS;
  assert(python && notebookPython && output && path.isAbsolute(output));
  fs.mkdirSync(output, { recursive: true });
  const root = fs.mkdtempSync(path.join(output, 'isolated-'));
  const config = path.join(root, 'notebook-config.json');
  fs.writeFileSync(config, JSON.stringify({ python: notebookPython, lab_url: 'http://127.0.0.1:8890/' }));
  const env = { ...process.env, PYTHONPATH: path.resolve('src'), PYTHONUTF8: '1', QUANT_STUDIO_NOTEBOOK_CONFIG: config };
  const seed = `import json,sys,hashlib
from pathlib import Path
from quant_studio.projects import ProjectStore
from quant_studio.notebooks import NotebookStore
from quant_studio.datasets import DatasetStore
root=Path(sys.argv[1]); source=root/'synthetic'; source.mkdir()
csv=source/'observations.csv'; csv.write_text('value\\n2\\n4\\n8\\n16\\n32\\n',encoding='utf-8')
(source/'manifest.json').write_text(json.dumps({'provider':'synthetic','files':{'observations.csv':hashlib.sha256(csv.read_bytes()).hexdigest()}}))
item=DatasetStore(root).register('合成数据：窗口比较','a-share-four-factor',str(source))
project=ProjectStore(root).save('合成样本研究','观察窗口变化怎样影响合成样本的均值？',datasets=[item['id']])
draft=NotebookStore(root).initialize(project['id'])
nb=json.loads(draft.read_text(encoding='utf-8'))
nb['cells'][-1]['source']="import csv\\nitem = context['datasets'][0]\\nwith (Path(item['path']) / 'observations.csv').open(encoding='utf-8') as stream:\\n    samples = [float(row['value']) for row in csv.DictReader(stream)]\\nselected = samples[:parameters['window']]\\nresearch_metrics = {'样本数': {'value': len(selected), 'unit': '个', 'definition': '窗口内的合成观测数'}, '样本均值': {'value': sum(selected)/len(selected), 'unit': '合成单位', 'definition': '窗口内合成观测的算术均值'}}\\nresearch_metrics"
draft.write_text(json.dumps(nb,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'project':project['id'],'dataset':item['id']}))`;
  const seeded = JSON.parse(execFileSync(python, ['-c', seed, root], { env, encoding: 'utf8', windowsHide: true }));
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const stop = path.join(root, 'stop');
  const child = spawn(python, ['-c', 'import sys;from quant_studio.server import serve;serve(port=int(sys.argv[1]),runs_root=sys.argv[2],stop_file=sys.argv[3])', String(port), root, stop], {
    env, windowsHide: true,
    stdio: ['ignore', fs.openSync(path.join(root, 'stdout.log'), 'w'), fs.openSync(path.join(root, 'stderr.log'), 'w')],
  });
  let browser;
  try {
    browser = await chromium.launch({ headless: true, ...(process.env.QUANT_BROWSER_EXECUTABLE ? { executablePath: process.env.QUANT_BROWSER_EXECUTABLE } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const base = `http://127.0.0.1:${port}`;
    let ready = false;
    for (let i = 0; i < 60; i++) {
      try { ready = (await page.goto(base + '/projects/' + seeded.project)).status() === 200; }
      catch (_) { await new Promise(resolve => setTimeout(resolve, 250)); }
      if (ready) break;
    }
    assert(ready, 'Server did not become ready');
    await page.getByText('把当前Notebook保存为研究模板', { exact: true }).click();
    await page.getByText('参数定义（模板作者填写一次）', { exact: true }).click();
    const form = page.locator('form[action$="/template-save"]');
    await form.locator('[name="name"]').fill('合成窗口研究模板');
    await form.locator('[name="parameters"]').fill(JSON.stringify({ window: { label: '观察窗口', type: 'integer', default: 2, min: 1, max: 5 } }));
    await Promise.all([page.waitForURL('**/projects/templates?template=*'), form.getByRole('button', { name: '保存为新模板版本' }).click()]);
    const templateURL = page.url();
    await page.screenshot({ path: path.join(output, 'template-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Template page overflows mobile');
    await page.screenshot({ path: path.join(output, 'template-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    const references = [];
    for (const window of [2, 4]) {
      await page.goto(templateURL);
      await page.locator('[name="name"]').fill('观察窗口' + window);
      await page.locator('[name="dataset"]').check();
      await page.locator('[name="param_window"]').fill(String(window));
      await Promise.all([page.waitForURL(/\/projects\/[a-f0-9]{32}$/), page.getByRole('button', { name: '创建研究项目', exact: true }).click()]);
      await Promise.all([page.waitForURL('**/jobs/*'), page.getByRole('button', { name: '冻结已保存草稿并提交实验' }).click()]);
      const resultURL = await page.getByRole('link', { name: '打开项目实验记录' }).getAttribute('href');
      const parts = resultURL.split('/');
      references.push(parts[2] + ':' + parts[4]);
      let succeeded = false;
      for (let i = 0; i < 90; i++) {
        await page.goto(base + resultURL);
        const body = await page.locator('body').innerText();
        if (body.includes('状态：succeeded')) { succeeded = true; break; }
        assert(!body.includes('状态：failed'), body);
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      assert(succeeded, 'Notebook did not succeed');
    }
    await page.goto(base + '/projects/notebook-compare');
    for (const ref of references) await page.locator(`input[name="run"][value="${ref}"]`).check();
    await Promise.all([page.waitForURL('**/projects/notebook-compare?*'), page.getByRole('button', { name: '比较所选实验', exact: true }).click()]);
    const rows = await page.locator('tr').allTextContents();
    assert(rows.some(r => r.includes('样本均值') && r.includes('3.0') && r.includes('7.5')), rows.join('\n'));
    assert(rows.some(r => r.includes('样本数') && r.includes('2') && r.includes('4')));
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('link', { name: '下载条件与指标CSV' }).click();
    await (await downloadPromise).saveAs(path.join(output, 'comparison.csv'));
    await page.screenshot({ path: path.join(output, 'comparison-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Comparison page overflows mobile');
    await page.screenshot({ path: path.join(output, 'comparison-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'acceptance.json'), JSON.stringify({ ok: true, root, references, browserErrors: errors }, null, 2));
    console.log(JSON.stringify({ ok: true, root, references }));
  } finally {
    if (browser) await browser.close();
    fs.writeFileSync(stop, 'stop');
    await Promise.race([new Promise(resolve => child.once('exit', resolve)), new Promise((_, reject) => setTimeout(() => reject(new Error('Preview did not stop')), 15000))]);
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
