// Compile product TypeScript in the test process; React itself is not mocked.
const ts = require('typescript');
const fs = require('node:fs');
function compile(module, filename) {
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX },
  });
  module._compile(output.outputText, filename);
}
require.extensions['.ts'] = compile;
require.extensions['.tsx'] = compile;
