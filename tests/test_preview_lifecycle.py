"""Exercise preview restoration without requiring a browser dependency."""

import json
import shutil
import subprocess

import pytest

from quant_studio.server import _CODE_SCRIPT


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js script harness unavailable"
)
def test_history_restoration_refreshes_preview_from_restored_form_values():
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const listeners = {};
const form = {value: '10000', dataset: {code: '/code'}, addEventListener() {}};
const compiled = {textContent: 'initial_capital: 10000'};
const requests = [];
const context = {
  document: {querySelector: key => key === 'form.flow' ? form : compiled},
  window: {addEventListener: (event, callback) => {listeners[event] = callback;}},
  FormData: class {constructor(form) {this.value = form.value;}
    *[Symbol.iterator]() {yield ['initial_capital', this.value];}},
  URLSearchParams,
  fetch: async (url, options) => {
    requests.push(options.body.get('initial_capital'));
    const value = options.body.get('initial_capital');
    return {text: async () => 'initial_capital: ' + value};
  }
};
vm.runInNewContext(SCRIPT, context);
// Browsers restore form controls after evaluating the page's initial script.
form.value = '26000';
if (listeners.pageshow) listeners.pageshow({persisted: true});
setImmediate(() => {
  assert.deepEqual(requests, ['26000']);
  assert.equal(compiled.textContent, 'initial_capital: 26000');
});
"""
    result = subprocess.run(
        ["node", "-e", "const SCRIPT = " + json.dumps(_CODE_SCRIPT) + ";\n" + harness],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
