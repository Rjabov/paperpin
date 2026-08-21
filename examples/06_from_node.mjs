// Using paperpin from Node today: spawn the CLI, read the JSON.
//
// paperpin is a Python engine (the OCR/PDF stack lives there), but the
// result is plain JSON with normalized 0..1 boxes, which makes the
// Node/React side trivial: render the page image, absolutely position
// divs at bbox * imageSize, color by status.
//
// Needs: pip install "paperpin[full]" somewhere on PATH, node >= 18.
// Run from the repo root:  node examples/06_from_node.mjs

import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, rmSync } from 'node:fs';

const extraction = {
  supplier_name: 'Havel & Kraus Paper s.r.o.',
  invoice_number: '2026-0847',
  total_due: '2 424.54',
  approved_by: 'M. Sedláčková',        // not on the paper
};
writeFileSync('extraction.json', JSON.stringify(extraction));

execFileSync('paperpin', [
  'ground', 'fixtures/demo/demo_invoice.pdf',
  '--extraction', 'extraction.json',
  '-o', 'result.json',
], { stdio: 'inherit' });

const result = JSON.parse(readFileSync('result.json', 'utf8'));

// the shape a React overlay needs, straight from the file:
for (const [name, f] of Object.entries(result.fields)) {
  console.log(name.padEnd(18), f.status.padEnd(12), f.bbox ?? '');
}
// e.g. position a pin in a viewer sized w x h:
//   const [x0, y0, x1, y1] = f.bbox;
//   style={{ left: x0*w, top: y0*h, width: (x1-x0)*w, height: (y1-y0)*h }}

const fake = result.fields.approved_by.status;
if (fake !== 'not_found') throw new Error('expected the fake to be flagged');
console.log('\nfabricated field flagged:', fake);

rmSync('extraction.json'); rmSync('result.json');
