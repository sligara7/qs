#!/usr/bin/env node
// Exercise the exact HTTP calls finch's queue-server client makes (src/api/qServer/requests.ts
// on finch main) against a running qs, and check the response shapes finch's types declare.
// This is the executable form of the finch-compatibility check; it needs only Node >= 18.
//
//   QS_URL=http://localhost:60610 QS_API_KEY=test node tools/finch_client_check.mjs
//
// It clears the queue and history, runs `count` and a `scan` on the loaded profile (the test
// profile in tests/profiles/minimal works), pauses, resumes and aborts. Do not point it at a
// beamline that is busy.

const BASE = (process.env.QS_URL || 'http://localhost:60610').replace(/\/$/, '') + '/api';
const KEY = process.env.QS_API_KEY || 'test';
const HEADERS = { Authorization: `ApiKey ${KEY}`, 'Content-Type': 'application/json' };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};
const missing = (o, keys) => keys.filter((k) => !(k in (o || {})));
async function get(path) {
  const r = await fetch(BASE + path, { headers: HEADERS });
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status} ${await r.text()}`);
  return r.json();
}
async function post(path, body = {}) {
  const r = await fetch(BASE + path, { method: 'POST', headers: HEADERS, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`POST ${path} -> ${r.status} ${await r.text()}`);
  return r.json();
}
async function waitFor(pred, timeoutMs = 60000, every = 150) {
  const deadline = Date.now() + timeoutMs;
  let s;
  while (Date.now() < deadline) {
    s = await get('/status');
    if (pred(s)) return s;
    await sleep(every);
  }
  return s;
}

const STATUS_KEYS = ['msg','items_in_queue','items_in_history','running_item_uid','manager_state','queue_stop_pending','queue_autostart_enabled','worker_environment_exists','worker_environment_state','worker_background_tasks','re_state','ip_kernel_state','ip_kernel_captured','pause_pending','run_list_uid','plan_queue_uid','plan_history_uid','devices_existing_uid','plans_existing_uid','devices_allowed_uid','plans_allowed_uid','plan_queue_mode','task_results_uid','lock_info_uid','lock'];

let s = await get('/status');
check('getStatus has every GetStatusResponse field', missing(s, STATUS_KEYS).length === 0, 'missing: ' + missing(s, STATUS_KEYS));

const plans = await get('/plans/allowed');
const firstPlan = Object.values(plans.plans_allowed)[0];
check('getPlansAllowed → Plan shape', plans.success && firstPlan && missing(firstPlan, ['name','properties','parameters','module']).length === 0 && firstPlan.parameters.every((x) => 'name' in x && 'kind' in x));
const devices = await get('/devices/allowed');
const firstDevice = Object.values(devices.devices_allowed)[0];
check('getDevicesAllowed → Device shape', devices.success && firstDevice && missing(firstDevice, ['is_readable','is_movable','is_flyable','classname','module']).length === 0);

const detector = Object.keys(devices.devices_allowed).find((n) => devices.devices_allowed[n].is_readable && !devices.devices_allowed[n].is_movable) || 'det';
const motor = Object.keys(devices.devices_allowed).find((n) => devices.devices_allowed[n].is_movable) || 'motor';

await post('/queue/clear');
await post('/history/clear');

const add = await post('/queue/item/add', { item: { item_type: 'plan', name: 'count', args: [[detector]], kwargs: { num: 2 } }, pos: 'back' });
check('addQueueItem → {success, qsize, item{item_uid,user,user_group}}', add.success && add.qsize === 1 && add.item.item_uid && 'user' in add.item && 'user_group' in add.item, add.msg);
const uid = add.item.item_uid;
const q = await get('/queue/get');
check('getQueue → {items, running_item, plan_queue_uid}', q.success && q.items.length === 1 && 'plan_queue_uid' in q && 'running_item' in q);
const gi = await get(`/queue/item/${uid}`);
check('getQueueItem (path style, finch main) → item', gi.success && gi.item.item_uid === uid, gi.msg);

check('startRE (/queue/start)', (await post('/queue/start')).success);
s = await waitFor((s) => s.items_in_history >= 1 && s.running_item_uid === null);
const hist = await get('/history/get');
check('getQueueHistory → result{exit_status,run_uids,time_start,time_stop,msg,traceback}', hist.success && hist.items.length === 1 && missing(hist.items[0].result, ['exit_status','run_uids','time_start','time_stop','msg','traceback']).length === 0 && hist.items[0].result.exit_status === 'success', hist.items[0]?.result?.exit_status);

// A scan opens a run and has checkpoints, so finch's option-less (deferred) pause can land.
const ex = await post('/queue/item/execute', { item: { item_type: 'plan', name: 'scan', args: [[detector], motor, -1, 1, 60] } });
check('executeQueueItem', ex.success && ex.item.item_uid, ex.msg);
s = await waitFor((s) => s.re_state === 'running', 15000);
check('running_item_uid reported while running', s.running_item_uid === ex.item.item_uid, `${s.running_item_uid}`);
await sleep(700);
const runs = await get('/re/runs/active');
check('getRunsActive → one open run during a scan', runs.success && runs.run_list.length === 1 && runs.run_list[0].is_open === true && 'uid' in runs.run_list[0], JSON.stringify(runs.run_list));
check('pauseRE with {} body (deferred, as finch sends it)', (await post('/re/pause')).success);
s = await waitFor((s) => s.re_state === 'paused', 20000);
check('status shows paused after the next checkpoint', s.re_state === 'paused' && s.manager_state === 'paused', `${s.re_state}/${s.manager_state}`);
check('resumeRE', (await post('/re/resume')).success);
s = await waitFor((s) => s.re_state === 'running', 15000);
check('abortRE', (await post('/re/abort')).success);
s = await waitFor((s) => s.re_state === 'idle' && s.running_item_uid === null && s.items_in_history === 2, 30000);
const hist2 = await get('/history/get');
check('aborted item recorded as abort and the queue stopped (stop-and-wait)', hist2.items.at(-1)?.result?.exit_status === 'abort' && s.manager_state === 'idle', `${hist2.items.at(-1)?.result?.exit_status} / ${s.manager_state}`);

const add2 = await post('/queue/item/add', { item: { item_type: 'plan', name: 'count', args: [[detector]] }, pos: 'back' });
const rm = await post('/queue/item/remove', { uid: add2.item.item_uid });
check('removeQueueItem → {success, item, qsize}', rm.success && rm.item.item_uid === add2.item.item_uid && rm.qsize === 0, rm.msg);
const env = await post('/environment/open');
check('openEnvironment tolerated → {success, task_uid}', env.success && 'task_uid' in env, env.msg);

const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} finch client calls behaved as finch expects`);
process.exit(failed ? 1 : 0);
