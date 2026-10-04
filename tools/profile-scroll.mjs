// Chrome DevTools Protocol scroll workload. No production dependencies.
// PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node tools/profile-scroll.mjs
//   URL OUTPUT_DIRECTORY [desktop|mobile|tablet] [CPU_RATE] [REPEATS] [optional diagnostic CSS file]
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const [url = 'http://127.0.0.1:8000', output = '/tmp/aigrow-profile', device = 'mobile', rate = '6', repeats = '3', diagnosticCSS] = process.argv.slice(2);
const scrollInput = process.env.SCROLL_INPUT || 'mouse';
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: !process.env.HEADED });
const browserCDP = await browser.newBrowserCDPSession();
const system = await browserCDP.send('SystemInfo.getInfo');
const viewport = device === 'desktop' ? { width: 1440, height: 900 }
  : device === 'tablet' ? { width: 768, height: 1024 } : { width: 393, height: 851 };
const context = await browser.newContext({ viewport, deviceScaleFactor: device === 'desktop' ? 1 : 2,
  isMobile: device !== 'desktop', hasTouch: device !== 'desktop' });
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send('Emulation.setCPUThrottlingRate', { rate: Number(rate) });
await cdp.send('Performance.enable');
await cdp.send('LayerTree.enable');
let layers = [];
cdp.on('LayerTree.layerTreeDidChange', event => { if (event.layers) layers = event.layers; });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const results = [];
try {
  // Warm font/network caches equally before each measured navigation.
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  for (let run = 1; run <= Number(repeats); run++) {
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    if (diagnosticCSS) await page.addStyleTag({ content: await fs.readFile(diagnosticCSS, 'utf8') });
    await page.waitForTimeout(1700);
    // Land just before Creator Selection so the original entrance still runs.
    const route = await page.evaluate(() => {
      const top = selector => document.querySelector(selector).getBoundingClientRect().top + scrollY;
      const start = Math.round(top('.framework') - innerHeight + 40);
      const end = Math.min(document.documentElement.scrollHeight - innerHeight,
        Math.round(top('.contact') + document.querySelector('.contact').offsetHeight - innerHeight / 2));
      scrollTo({ top: start, behavior: 'instant' });
      return { start, end, distance: end - start, viewport: [innerWidth, innerHeight] };
    });
    if (process.env.SCROLL_DISTANCE) {
      route.distance = Number(process.env.SCROLL_DISTANCE);
      route.end = route.start + route.distance;
    }
    await page.waitForTimeout(400);
    const layerSnapshot = async () => Promise.all(layers.filter(l => l.drawsContent).map(async layer => ({
      width: layer.width, height: layer.height, backendNodeId: layer.backendNodeId,
      reasons: (await cdp.send('LayerTree.compositingReasons', { layerId: layer.layerId }).catch(() => ({}))).compositingReasons
    })));
    const startLayers = await layerSnapshot();
    await page.evaluate(() => {
      window.__scrollProfile = { frames: [], longTasks: [], running: true };
      const profile = window.__scrollProfile;
      profile.observer = new PerformanceObserver(list => {
        profile.longTasks.push(...list.getEntries().map(e => ({ start: e.startTime, duration: e.duration })));
      });
      profile.observer.observe({ type: 'longtask' });
      let previous;
      const tick = now => {
        if (previous !== undefined) profile.frames.push(now - previous);
        previous = now;
        if (profile.running) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      performance.mark('scroll-profile-start');
    });
    await cdp.send('Tracing.start', { transferMode: 'ReturnAsStream', categories:
      'devtools.timeline,disabled-by-default-devtools.timeline,disabled-by-default-devtools.timeline.frame,disabled-by-default-devtools.timeline.stack,blink.user_timing,toplevel,cc' +
      (process.env.PROFILE_INVALIDATIONS ? ',disabled-by-default-devtools.timeline.invalidationTracking' : '') });
    const before = await cdp.send('Performance.getMetrics');
    const started = Date.now();
    // Real compositor scroll gestures, not scrollTo in an application RAF loop.
    const gesture = distance => cdp.send('Input.synthesizeScrollGesture', {
      x: Math.round(viewport.width / 2), y: Math.round(viewport.height * .6), yDistance: distance,
      speed: 1800, gestureSourceType: scrollInput, preventFling: true,
    });
    await gesture(-route.distance);
    await page.waitForTimeout(250);
    const contactLayers = await layerSnapshot();
    await gesture(route.distance);
    await gesture(-route.distance);
    const elapsedMs = Date.now() - started;
    const after = await cdp.send('Performance.getMetrics');
    const raw = await page.evaluate(() => {
      const profile = window.__scrollProfile;
      profile.running = false;
      profile.observer.disconnect();
      performance.mark('scroll-profile-end');
      return { frames: profile.frames.slice(), longTasks: profile.longTasks.slice(), scrollY };
    });
    const completed = new Promise(resolve => cdp.once('Tracing.tracingComplete', resolve));
    await cdp.send('Tracing.end');
    const { stream } = await completed;
    let trace = '';
    for (;;) {
      const chunk = await cdp.send('IO.read', { handle: stream });
      trace += chunk.data;
      if (chunk.eof) break;
    }
    await cdp.send('IO.close', { handle: stream });
    const events = JSON.parse(trace).traceEvents;
    const mainThread = events.find(e => e.name === 'thread_name' && e.args?.name === 'CrRendererMain');
    const main = events.filter(e => e.pid === mainThread?.pid && e.tid === mainThread?.tid);
    const totals = {};
    for (const name of ['Layout', 'UpdateLayoutTree', 'Paint', 'PrePaint', 'FireAnimationFrame', 'FunctionCall', 'EventDispatch', 'RunTask']) {
      const matches = main.filter(e => e.name === name && e.ph === 'X');
      totals[name] = { count: matches.length, ms: +(matches.reduce((sum, e) => sum + (e.dur || 0), 0) / 1000).toFixed(2),
        maxMs: +(Math.max(0, ...matches.map(e => e.dur || 0)) / 1000).toFixed(2) };
    }
    const metricMap = list => Object.fromEntries(list.metrics.map(e => [e.name, e.value]));
    const b = metricMap(before), a = metricMap(after);
    const metrics = Object.fromEntries(['TaskDuration', 'ScriptDuration', 'LayoutDuration', 'RecalcStyleDuration', 'LayoutCount', 'RecalcStyleCount']
      .map(key => [key, a[key] - b[key]]));
    const frames = raw.frames.slice().sort((a, b) => a - b);
    const percentile = fraction => frames[Math.min(frames.length - 1, Math.floor(frames.length * fraction))];
    const result = { run, route, elapsedMs, metrics, totals,
      frames: { count: frames.length, p50Ms: percentile(.5), p95Ms: percentile(.95), maxMs: frames.at(-1),
        over25Ms: frames.filter(n => n > 25).length, over50Ms: frames.filter(n => n > 50).length },
      longTasks: raw.longTasks,
      forcedLayouts: main.filter(e => e.name === 'Layout' && e.args?.beginData?.stackTrace?.length).length,
      traceDroppedFrameEvents: events.filter(e => e.name === 'DroppedFrame').length,
      tracePartialFrameEvents: events.filter(e => e.name === 'DroppedFrame' && e.args?.hasPartialUpdate).length,
      startLayers, contactLayers, endScrollY: raw.scrollY };
    results.push(result);
    await fs.writeFile(path.join(output, `trace-${run}.json`), trace);
    await fs.writeFile(path.join(output, `frames-${run}.json`), JSON.stringify(raw, null, 2));
    await page.screenshot({ path: path.join(output, `contact-${run}.png`) });
    console.log(JSON.stringify({ device, rate, run, elapsedMs, frames: result.frames, metrics, totals, longTasks: raw.longTasks.length }));
  }
} finally {
  await fs.writeFile(path.join(output, 'summary.json'), JSON.stringify({ url, device, scrollInput, rate: Number(rate), viewport,
    browser: browser.version(), gpu: system.gpu, errors, results }, null, 2));
  await browser.close();
}
