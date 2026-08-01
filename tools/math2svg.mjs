// Maths -> SVG batch converter, run offline by journal2epub.
//
// Accepts either input form, because publishers differ: GigaScience deposits
// `<tex-math>` and no MathML, PLOS deposits MathML and no TeX. Both render to
// the same SVG so there is exactly one output path downstream.
//
// SVG rather than MathML output: MathML support across e-readers is thin and
// inconsistent, while SVG renders identically everywhere including e-ink.
// Sizes come back in `ex` units so the maths scales with the reader's chosen
// font size.
//
// Protocol: one JSON object per line on stdin -> one JSON object per line on
// stdout. Each request is {id, display, tex} or {id, display, mathml}.
// Batching keeps node's startup cost off the per-expression path.
import { mathjax } from 'mathjax-full/js/mathjax.js';
import { TeX } from 'mathjax-full/js/input/tex.js';
import { MathML } from 'mathjax-full/js/input/mathml.js';
import { SVG } from 'mathjax-full/js/output/svg.js';
import { liteAdaptor } from 'mathjax-full/js/adaptors/liteAdaptor.js';
import { RegisterHTMLHandler } from 'mathjax-full/js/handlers/html.js';
import { AllPackages } from 'mathjax-full/js/input/tex/AllPackages.js';
import { createInterface } from 'node:readline';

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);

const svg = new SVG({ fontCache: 'local' });
const texDoc = mathjax.document('', {
  InputJax: new TeX({ packages: AllPackages, inlineMath: [], displayMath: [] }),
  OutputJax: svg,
});
const mmlDoc = mathjax.document('', {
  InputJax: new MathML({}),
  OutputJax: svg,
});

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  const s = line.trim();
  if (!s) continue;
  let req;
  try {
    req = JSON.parse(s);
  } catch (e) {
    // Never drop a request silently: the caller must be able to account for
    // every expression it sent.
    process.stdout.write(JSON.stringify({ id: null, error: 'bad-request: ' + e.message }) + '\n');
    continue;
  }
  const out = { id: req.id };
  try {
    const isMathML = typeof req.mathml === 'string' && req.mathml.length > 0;
    const doc = isMathML ? mmlDoc : texDoc;
    const src = isMathML ? req.mathml : req.tex;
    const node = doc.convert(src, { display: !!req.display, em: 16, ex: 8 });
    out.svg = adaptor.innerHTML(node);
    // In SVG output an input error is a node tagged data-mml-node="merror",
    // not a literal <merror> element.
    if (out.svg.includes('data-mml-node="merror"')) {
      out.error = 'math-error';
      const t = adaptor.textContent(node);
      if (t) out.error += ': ' + t.replace(/\s+/g, ' ').trim().slice(0, 120);
    }
  } catch (e) {
    out.error = String(e && e.message ? e.message : e);
  }
  process.stdout.write(JSON.stringify(out) + '\n');
}
