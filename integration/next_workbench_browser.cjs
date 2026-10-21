// Optional browser acceptance. All state is created in a fresh output directory.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const net = require('node:net');

async function main() {
  const [python, notebookPython, output] = process.argv.slice(2);
  if (!python || !notebookPython || !output) throw Error('python notebook-python output required');
  await fs.mkdir(output, {recursive: true});
  const state = await fs.mkdtemp(path.join(output, 'isolated-'));
  const config = path.join(state, 'notebook.json');
  await fs.writeFile(config, JSON.stringify({python: notebookPython, lab_url: 'http://127.0.0.1:8890'}));
  const listener = net.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const server = spawn(python, ['-m', 'quant_studio', 'serve', '--port', String(port),
    '--runs-root', path.join(state, 'runs')], {windowsHide: true,
    env: {...process.env, QUANT_STUDIO_NOTEBOOK_CONFIG: config, PYTHONUTF8: '1'}});
  let serverLogs = '';
  server.stdout.on('data', data => {serverLogs += data});
  server.stderr.on('data', data => {serverLogs += data});
  const base = `http://127.0.0.1:${port}`;
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  try {
    for (let i = 0; i < 60; i++) {
      try {if ((await page.goto(base)).status() === 200) break;} catch {}
      await new Promise(r => setTimeout(r, 250));
    }
    await page.goto(base + '/projects/new');
    const create = page.locator('form[action="/projects/new"]');
    await create.getByLabel('项目名称', {exact: true}).fill('窗口研究：界面验收');
    await create.getByLabel('想回答的问题').fill('比较5、10、20、60日窗口');
    await create.getByRole('button', {name: '创建研究并调整方案'}).click();
    await page.waitForURL(/research\/[a-f0-9]+\?project=/);
    const projectId = new URL(page.url()).searchParams.get('project');
    if (!projectId) throw Error('Created recipe did not retain the project');
    const projectUrl = base + '/projects/' + projectId;
    await page.goto(projectUrl + '?tab=plan');
    await page.getByText('高级：交互研究与批量实验', {exact: true}).click();
    await page.getByRole('button', {name: '创建草稿或刷新数据上下文'}).click();
    await page.waitForURL(projectUrl + '?tab=plan');
    await page.getByText('高级：交互研究与批量实验', {exact: true}).click();
    await page.getByText('把当前Notebook保存为研究模板', {exact: true}).click();
    const template = page.locator('form[action$="/template-save"]');
    await template.getByText('参数定义（模板作者填写一次）', {exact: true}).click();
    await template.getByLabel('参数定义JSON').fill(JSON.stringify({window: {
      label: '观察窗口', type: 'integer', default: 20, min: 1, max: 120}}));
    await template.getByRole('button', {name: '保存为新模板版本'}).click();
    await page.waitForURL(/projects\/templates\?template=/);
    const templateId = new URL(page.url()).searchParams.get('template');
    const iterations = projectUrl + '/iterations?template=' + templateId;
    await page.goto(iterations);
    await page.getByLabel('观察窗口').fill('[5,10,20,60]');
    await page.getByRole('button', {name: '冻结输入并预览候选'}).click();
    await page.waitForURL(/iterations\?template=/);
    await page.getByText('4组参数', {exact: false}).waitFor();
    const checks = [];
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 1000});
      for (const [name, url] of [['iterations', iterations], ['data', base + '/data-center'],
                                  ['history', base + '/history'], ['delivery', base + '/delivery']]) {
        const response = await page.goto(url);
        if (response.status() !== 200) throw Error(`${name}: HTTP ${response.status()}`);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
        if (overflow) throw Error(`${name} overflows at ${width}px`);
        await page.screenshot({path: path.join(output, `${name}-${width}.png`), fullPage: true});
        checks.push({page: name, width, status: response.status(), overflow});
      }
    }
    if (errors.length) throw Error(errors.join('\n'));
    await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify({checks, errors, state}, null, 2));
    console.log(JSON.stringify({checked: checks.length, output}));
  } finally {
    await browser.close();
    server.kill();
    await fs.writeFile(path.join(output, 'server.log'), serverLogs);
  }
}
main().catch(error => {console.error(error); process.exitCode = 1});
