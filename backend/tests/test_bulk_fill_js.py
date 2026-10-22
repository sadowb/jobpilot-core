"""Execute batch writes against DOM doubles: preservation and forbidden controls."""

import json
import shutil
import subprocess
import unittest
from typing import Any

from jobpilot.browser.bulk import bulk_fill_js

NODE = shutil.which("node")
DOM = r"""
const {script,spec}=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
let events=0;
class Input {
  constructor(s){this.spec=s;this.id=s.id;this.name=s.id;this.tagName='INPUT';this.type=s.type||'text';this.raw=s.value||'';this.labels=[];this.nodeType=1;this.parentElement=null;this.isConnected=true;this.disabled=!!s.disabled;this.readOnly=!!s.readOnly;this.maxLength=s.maxLength||-1;this.validity={valid:s.valid!==false};}
  get value(){return this.raw;} set value(v){this.raw=v;}
  getAttribute(k){return ({'aria-label':this.spec.label,role:this.spec.role,autocomplete:this.spec.autocomplete})[k]||null;}
  closest(s){return s==='fieldset' && this.spec.reference?{querySelector:()=>({textContent:'Professional reference'})}:null;}
  matches(){return false;}
  getClientRects(){return this.spec.hidden?[]:[1];}
  dispatchEvent(){events++;if(this.spec.discard)this.raw='';}
}
class Select extends Input {
  constructor(s){super(s);this.tagName='SELECT';this.multiple=!!s.multiple;this.options=s.options||[];this.raw=s.value||'';}
  get selectedOptions(){return this.options.filter(o=>o.value===this.raw);}
  get value(){return this.raw;}set value(v){this.raw=v;}
}
global.HTMLInputElement=global.HTMLTextAreaElement=Input;global.HTMLSelectElement=Select;
global.Event=class{constructor(type){this.type=type;}};
global.getComputedStyle=e=>({visibility:e.spec.hidden?'hidden':'visible'});
const nodes=spec.map(s=>s.select?new Select(s):new Input(s));
global.document={querySelector:s=>nodes.find(e=>s==='#'+e.id)||null,getElementById:id=>nodes.find(e=>e.id===id),querySelectorAll:()=>[]};
global.CSS={escape:s=>s};
const result=eval(script);
process.stdout.write(JSON.stringify({result,values:nodes.map(e=>e.value),events}));
"""


@unittest.skipUnless(NODE, "node required for DOM helper tests")
class BulkJavaScriptTests(unittest.TestCase):
    def run_js(self, spec: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
        result = subprocess.run(
            [NODE or "node", "-e", DOM],
            input=json.dumps({"script": bulk_fill_js(items), "spec": spec}),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return json.loads(result.stdout)

    def test_native_setter_events_and_readback_happen_in_one_batch(self) -> None:
        result = self.run_js(
            [{"id": "name", "label": "Full name"}, {"id": "email", "label": "Email", "type": "email"}],
            [
                {"selector": "#name", "label": "Full name", "value": "Ada Lovelace"},
                {"selector": "#email", "label": "Email", "value": "ada@example.test"},
            ],
        )
        self.assertEqual(result["values"], ["Ada Lovelace", "ada@example.test"])
        self.assertEqual(result["events"], 4)
        self.assertEqual([r["status"] for r in result["result"]], ["filled", "filled"])

    def test_credentials_consent_custom_hidden_readonly_and_changed_labels_are_not_written(self) -> None:
        specs = [
            {"type": "password"},
            {"label": "I agree to terms"},
            {"role": "combobox"},
            {"hidden": True},
            {"readOnly": True},
            {"label": "Different field"},
            {"reference": True},
        ]
        specs = [{"id": str(i), "label": "Full name", **s} for i, s in enumerate(specs)]
        items = [
            {"selector": "#" + str(i), "label": s["label"] if i != 5 else "Full name", "value": "Ada"}
            for i, s in enumerate(specs)
        ]
        result = self.run_js(specs, items)
        self.assertEqual(result["events"], 0)
        self.assertEqual(result["values"], [""] * len(specs))

    def test_existing_values_are_never_overwritten_and_invalid_matches_are_not_reported_filled(self) -> None:
        specs = [
            {"id": "a", "label": "Email", "value": "owner@example.test"},
            {"id": "b", "label": "Email", "value": "ada@example.test"},
            {"id": "c", "label": "Email", "value": "ada@example.test", "valid": False},
        ]
        result = self.run_js(specs, [{"selector": "#" + s["id"], "label": "Email", "value": "ada@example.test"} for s in specs])
        self.assertEqual(result["values"], ["owner@example.test", "ada@example.test", "ada@example.test"])
        self.assertEqual(result["events"], 0)
        self.assertEqual([r["status"] for r in result["result"]], ["preserved", "filled", "preserved"])

    def test_ambiguous_select_options_and_rejected_react_values_are_not_reported_filled(self) -> None:
        select = {
            "id": "country",
            "label": "Country",
            "select": True,
            "options": [
                {"text": "Choose", "value": "", "disabled": True},
                {"text": "United States", "value": "us1"},
                {"text": "United States", "value": "us2"},
            ],
        }
        result = self.run_js(
            [select, {"id": "email", "label": "Email", "discard": True}],
            [
                {"selector": "#country", "label": "Country", "value": "United States"},
                {"selector": "#email", "label": "Email", "value": "ada@example.test"},
            ],
        )
        self.assertEqual([r["status"] for r in result["result"]], ["skipped", "failed"])
        self.assertEqual(result["values"], ["", ""])
