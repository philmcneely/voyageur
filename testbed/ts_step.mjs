/*
===============================================================================
One heal through the TypeScript client (the step run_acceptance.py --client ts runs per failing statement)
===============================================================================
Reads one JSON request on stdin (see testbed/ts_client.py heal_step) and runs the TS client's own Healer on it, in the project
directory the Python side prepared (a copy of the client's repository files and the TS suite): the client reopens the failing
route, runs its vendored probe, builds the request from its fingerprint file, checks each candidate live, applies it with its
safety checks and re-runs the exact test through `verify`, which reports the re-run as the client's verifyRun does ({passed,
failure}); the client sends a later failure as failed_later and the service judges it, as in the Python run.

The only things the client does not do itself are the stand-ins of a test bench: when the service's model is scripted, the
golden element is found in the page the client opened (its TS expression, from the golden file, run with `page`) and its
inventory ref is posted to the service's /_testbed/script route, so the scripted model picks what the golden file names.

Prints one line: RESULT <json> with the decision, the golden element's ref and the ref the written locator was checked against.

Author: PMAC
===============================================================================
*/
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const input = JSON.parse(readFileSync(0, "utf8"));
process.chdir(input.projectDir);
const load = (name) => import(pathToFileURL(join(input.projectDir, "scripts", name)).href);
const { Healer, LivePage, ServiceClient, matchRef, parseCapture, settingsFromEnv } = await load("heal-client.mjs");
const { cleanInventory } = await load("heal-redact.mjs");
const { probeFunction } = await load("heal-probe.mjs");
const { runSuite } = await load("heal.mjs");

const env = {
  HEAL_SERVICE_URL: input.serviceUrl, BASE_URL: input.baseUrl, HEAL_PROJECT: "", QA_BASE_URL: input.appUrl,
  QA_ACTION_TIMEOUT_MS: String(input.timeoutMs), PATH: process.env.PATH, HOME: process.env.HOME,
};
Object.assign(process.env, env);
const settings = settingsFromEnv(env);
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const note = { targetRef: null, targetNote: null, targetHidden: null, targetFacts: null };
// the failing page as the test left it, read the way scripts/heal.mjs reads the attachment
const capture = parseCapture(input.failure.captureText, input.baseUrl);

/** The golden element in the page the client opened: its inventory ref, or null (the element is gone, or not listed). */
/** The golden TS expression with its LAST getByRole asked to include hidden elements (a scoping getByRole before it stays as it
 * is, so a hidden dialog or menu elsewhere does not widen the chain). Unchanged when it has none. */
export function includeHidden(expression) {
  const at = expression.lastIndexOf("getByRole(");
  if (at < 0) return expression;
  const m = /^getByRole\((\s*(['"])[^'"]*\2)\s*(,\s*\{)?/.exec(expression.slice(at));
  if (!m) return expression;
  const head = m[3] ? `getByRole(${m[1]}, { includeHidden: true, ` : `getByRole(${m[1]}, { includeHidden: true }`;
  return expression.slice(0, at) + head + expression.slice(at + m[0].length);
}

async function scriptTarget(live, probe) {
  // the golden element is looked up for every run, so a real-model run is judged too; only the scripted pick needs scriptUrl
  let ref = null;
  if (input.targetTs) {
    try {
      const locate = (expression) => {
        const loc = new Function("page", `return (${expression});`)(live.page); // trusted expressions from the golden files
        return typeof loc.owner === "function" && !("evaluate" in loc) ? loc.owner() : loc; // a frame hop: the frame's element
      };
      const describe = (loc) => loc.first().evaluate(probeFunction(probe), undefined, { timeout: 2000 });
      let data;
      try {
        data = await describe(locate(input.targetTs));
      } catch (e) {
        // getByRole leaves out what is not in the accessibility tree (a closed popover, a collapsed menu): the reopened page
        // shows the step's target closed, so look it up again with hidden elements included, and only when that finds exactly
        // one element (a hidden duplicate or a leftover of a removed element would make the key wrong either way). Only the
        // lookup of the golden element changes; the client and the service see the page as it is. The row says so.
        const hidden = includeHidden(input.targetTs);
        const loc = hidden === input.targetTs ? null : locate(hidden);
        if (!loc || (await loc.count()) !== 1) throw e;
        data = await describe(loc);
        note.targetNote = "golden element found with hidden elements included";
      }
      const item = cleanInventory(data?.items ?? [])[0];
      ref = item ? matchRef(capture?.inventory ?? live.inventory, item, { captured: Boolean(capture) }) : null; // the listing the request carries
      if (!ref) note.targetNote = item ? "the golden element is not in the probe listing" : "the golden element is not describable";
    } catch (e) {
      note.targetNote = `${e?.name ?? "Error"}: ${String(e?.message ?? e).slice(0, 300)}`;
    }
  }
  note.targetRef = ref;
  const item = ref ? live.inventory.find((i) => i.ref === ref) : null;
  note.targetHidden = item ? Boolean(item.hidden) : null; // the golden element is there but not shown
  note.targetFacts = item ? { hidden: Boolean(item.hidden), box: Boolean(item.box), top: item.top_at_center ?? null } : null;
  if (!input.scriptUrl) return;
  await fetch(input.scriptUrl, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script: ref ? [ref, ref, ref] : [null] }) });
}

/** The re-run as the client's own verifyRun reports it: {passed, failure}. The client decides whether a failure is later than the
 * patch and reports it; the service judges whether that keeps the heal. */
async function verify(failure, patched) {
  const { report } = runSuite({ grep: escapeRe(failure.title), file: input.file, env: process.env });
  if (!report) return { passed: false, failure: null };
  const walk = (suite, out) => {
    for (const s of suite.suites ?? []) walk(s, out);
    for (const spec of suite.specs ?? []) if (spec.title === failure.title) out.push(spec);
    return out;
  };
  const spec = (report.suites ?? []).flatMap((s) => walk(s, []))[0];
  const last = spec?.tests?.[0]?.results?.at(-1);
  if (!last) return { passed: false, failure: null };
  if (last.status === "passed") return { passed: true, failure: null };
  const err = last.errors?.[0] ?? {};
  const message = String(err.message ?? "");
  if (process.env.TS_STEP_DEBUG) console.error(JSON.stringify({ verify: failure.title, patched, line: err.location?.line, message: message.slice(0, 400) }));
  return { passed: false, failure: { ...failure, error: message, stack: String(err.stack ?? ""), location: err.location ?? null } };
}

if (process.env.TS_STEP_DEBUG) { // TS_STEP_DEBUG_FETCH: show why a call to the service failed
  const real = globalThis.fetch;
  globalThis.fetch = async (...a) => { try { return await real(...a); } catch (e) { console.error("fetch failed:", e?.cause?.code, e?.cause?.message ?? e?.message, "body bytes:", String(a[1]?.body ?? "").length); throw e; } };
}
const service = new ServiceClient(input.serviceUrl, "", { env: {} });
if (process.env.TS_STEP_DEBUG) {
  const heal = service.heal.bind(service);
  const result = service.result.bind(service);
  service.heal = async (r) => { const out = await heal(r); console.error(JSON.stringify({ heal: out.status, code: out.candidate?.code, patch: out.candidate?.patch?.to })); return out; };
  service.result = async (id, rep) => { const out = await result(id, rep); console.error(JSON.stringify({ report: rep, next: out.status, code: out.candidate?.code, reason: out.reason })); return out; };
}
const liveFactory = async (route, probe) => {
  const live = await LivePage.open(settings, route, probe);
  const snapshot = live.snapshot.bind(live);
  const check = live.check.bind(live);
  live.check = async (expression) => {
    const out = await check(expression);
    if (process.env.TS_STEP_DEBUG) console.error(JSON.stringify({ check: expression, result: out }));
    return out;
  };
  live.snapshot = async () => {
    const snap = await snapshot();
    await scriptTarget(live, probe);
    return snap;
  };
  return live;
};
const healer = new Healer(service, settings, verify, {
  root: input.projectDir, runId: input.runId, liveFactory, env: { HEAL_DIR: input.fingerprints ? ".heal" : ".heal-none" },
});
const { captureText, ...reported } = input.failure;
const failure = { ...reported, file: resolve(input.projectDir, input.file), capture };
const decision = await healer.heal(failure);
console.log("RESULT " + JSON.stringify({ decision, targetRef: note.targetRef, targetNote: note.targetNote, targetHidden: note.targetHidden, targetFacts: note.targetFacts }));
