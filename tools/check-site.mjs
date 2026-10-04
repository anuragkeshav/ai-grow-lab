// Browser regression checks. Provider calls are intercepted: never sends live leads.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tools/check-site.mjs URL [SCREENSHOT_DIR]
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const [url = 'http://127.0.0.1:8000', output = '/tmp/aigrow-checks'] = process.argv.slice(2);
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome' });
let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };
try {
  for (const width of [1440, 768, 393, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 900 },
      isMobile: width < 901, hasTouch: width < 901, deviceScaleFactor: width < 901 ? 2 : 1 });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(1700);
    check(await page.evaluate(() => document.documentElement.scrollWidth === innerWidth), `no overflow at ${width}`);
    const jump = async y => {
      await page.evaluate(top => scrollTo({ top, behavior: 'instant' }), y);
      await page.waitForTimeout(180);
    };
    await jump(41);
    check(await page.locator('.nav-wrap').evaluate(e => e.classList.contains('scrolled')), 'header threshold');
    await jump(601);
    check(await page.locator('.back-top').evaluate(e => e.classList.contains('show')), 'back-to-top threshold');
    // Jump over entire sections in both directions (point sentinels would miss this).
    for (const id of ['faq', 'services', 'solutions', 'method']) {
      const y = await page.locator('#' + id).evaluate(e => e.getBoundingClientRect().top + scrollY - 198);
      await jump(y);
      check(await page.locator(`.nav-links > a[href="#${id}"]`).evaluate(e => e.classList.contains('active')), `nav jump ${id}`);
    }
    await page.locator('.selection-board').scrollIntoViewIfNeeded();
    await page.waitForTimeout(1600);
    check(await page.locator('.selection-board').evaluate(e => getComputedStyle(e).willChange === 'auto' && e.getAnimations().length === 0), 'reveal cleanup releases hint without phantom transition');
    check(await page.locator('.signal').count() === 5, 'all creator cards retained');
    check(await page.locator('.signal-audience').evaluate(e => getComputedStyle(e).transform !== 'none') === (width > 680), 'original responsive rotations retained');
    await page.screenshot({ path: path.join(output, `creator-${width}.png`) });
    if (width === 1440) {
      await page.locator('.signal-audience').hover();
      await page.waitForTimeout(450);
      check(await page.locator('.signal-audience').evaluate(e => getComputedStyle(e).transform.includes('-5, -7')), 'card hover movement retained');
      await page.mouse.move(0, 0);
    }
    await page.locator('#contact-form').scrollIntoViewIfNeeded();
    await page.waitForTimeout(1600);
    check(await page.locator('.contact-gradient').evaluate(e => getComputedStyle(e, '::before').animationName === 'contact-shift'), 'gradient still animated');
    check(await page.locator('.hero').evaluate(e => e.classList.contains('motion-paused')), 'offscreen hero work paused');
    check(await page.locator('.form-note').textContent() === 'Your information is used only to respond to this request.', 'privacy copy');
    check((await page.locator('#submit-lead').textContent()).includes('Request a Discovery Call'), 'primary CTA');
    await page.screenshot({ path: path.join(output, `contact-${width}.png`) });
    // Resize a textarea and expand FAQ content: threshold/progress geometry must stay current.
    await page.locator('#message').evaluate(e => { e.style.height = '240px'; });
    await page.locator('details').first().evaluate(e => { e.open = true; });
    await page.waitForTimeout(200);
    await jump(0);
    check(await page.locator('.nav-links > a.active').count() === 0, 'nav resets at top after geometry change');
    check(await page.locator('.back-top.show').count() === 0, 'back-to-top resets');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.locator('#contact-form').scrollIntoViewIfNeeded();
    await page.waitForTimeout(200);
    check(await page.locator('.contact-gradient').evaluate(e => getComputedStyle(e, '::before').animationName === 'none'), 'reduced-motion gradient');
    check(await page.locator('.selection-board').evaluate(e => getComputedStyle(e).opacity === '1'), 'reduced-motion content visible');
    check(await page.locator('#scroll-progress').evaluate(e => parseFloat(e.style.transform.slice(7)) > .8), 'reduced-motion progress fallback');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.waitForTimeout(100);
    check(await page.locator('#scroll-progress').evaluate(e => e.style.transform === ''), 'native timeline restored on preference change');
    // Real browser validation and submit lifecycle, with an isolated fake API.
    const submissions = [];
    let responseStatus = 201;
    let responseBody = JSON.stringify({ ok: true, email_status: 'accepted', message: "Thanks — we'll reply within 24 hours." });
    let abortRequest = false;
    let responseHeaders = {};
    await page.route('**/api/leads', async route => {
      submissions.push(route.request().postDataJSON());
      await new Promise(resolve => setTimeout(resolve, 200));
      if (abortRequest) return route.abort('failed');
      await route.fulfill({ status: responseStatus, contentType: 'application/json', headers: responseHeaders, body: responseBody });
    });
    const fill = async () => {
      await page.locator('#name').fill('Test Founder');
      await page.locator('#company').fill('Example Brand');
      await page.locator('#email').fill('founder@example.com');
      await page.locator('#goal').selectOption('Create UGC');
      await page.locator('#message').fill('A new campaign with creator-led content.');
    };
    await page.locator('#contact-form').evaluate(form => form.requestSubmit());
    await page.waitForTimeout(100);
    check(submissions.length === 0, 'invalid empty form is not submitted');
    await fill();
    await page.locator('#contact-form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
    check(await page.locator('#submit-lead').isDisabled(), 'CTA disabled until API finishes');
    check(await page.locator('.form-status').textContent() === '', 'no premature success');
    await page.waitForTimeout(500);
    check(submissions.length === 1, 'rapid duplicate submit guarded');
    assert.deepEqual(submissions[0], { name: 'Test Founder', company: 'Example Brand', email: 'founder@example.com',
      goal: 'Create UGC', message: 'A new campaign with creator-led content.', website: '' }); checks++;
    check((await page.locator('.form-status').textContent()).includes('24 hours'), 'success message');
    check(await page.locator('#name').inputValue() === '', 'reset after success');
    responseStatus = 429;
    responseHeaders = { 'Retry-After': '1' };
    responseBody = JSON.stringify({ code: 'rate_limited', retry_after: 1, email_status: 'not_attempted' });
    await fill();
    await page.locator('#contact-form').evaluate(form => form.requestSubmit());
    await page.waitForTimeout(400);
    check(await page.locator('#name').inputValue() === 'Test Founder', 'rate limit preserves input');
    check(await page.locator('#submit-lead').isDisabled(), 'rate limit keeps CTA disabled during cooldown');
    check(await page.locator('.form-status').textContent() === 'Too many requests. Please wait 1 second before trying again. This request was not sent.', 'rate limit is distinct from delivery failure');
    const limitedCount = submissions.length;
    await page.locator('#contact-form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
    await page.waitForTimeout(100);
    check(submissions.length === limitedCount, 'cooldown blocks repeat clicks without requests');
    await page.waitForFunction(() => !document.getElementById('submit-lead').disabled);
    check(submissions.length === limitedCount, 'cooldown never auto-resubmits');
    check(await page.locator('.form-status').textContent() === 'You can try sending your request again.', 'cooldown expires normally');
    responseHeaders = {};
    const failure = "We couldn't send your request. Please try again or email us directly.";
    for (const [label, code, body, abort] of [
      ['Resend failure', 502, JSON.stringify({ error: failure }), false],
      ['configuration failure', 503, JSON.stringify({ error: failure }), false],
      ['proxy HTML', 502, '<html>Bad gateway</html>', false],
      ['legacy queued success', 201, JSON.stringify({ ok: true, message: 'Queued' }), false],
      ['2xx error', 200, JSON.stringify({ ok: false, email_status: 'accepted' }), false],
      ['empty JSON', 200, 'null', false],
      ['network failure', 200, '{}', true],
    ]) {
      responseStatus = code;
      responseBody = body;
      abortRequest = abort;
      await page.locator('#contact-form').evaluate(form => form.requestSubmit());
      await page.waitForFunction(() => !document.getElementById('submit-lead').disabled);
      check(await page.locator('.form-status').textContent() === failure, `${label}: exact failure message`);
      check(await page.locator('#name').inputValue() === 'Test Founder', `${label}: preserves input`);
    }
    abortRequest = false;
    responseStatus = 429;
    responseBody = '<html>Rate limited by proxy</html>';
    responseHeaders = { 'Retry-After': new Date(Date.now() + 2500).toUTCString() };
    await page.locator('#contact-form').evaluate(form => form.requestSubmit());
    await page.waitForTimeout(400);
    check((await page.locator('.form-status').textContent()).startsWith('Too many requests.'), 'non-JSON 429 remains a rate-limit error');
    check(await page.locator('#submit-lead').isDisabled(), 'HTTP-date Retry-After is honored');
    await page.waitForFunction(() => !document.getElementById('submit-lead').disabled);
    responseHeaders = {};
    responseBody = JSON.stringify({ error: 'Please wait a few minutes before sending another request.' });
    await page.locator('#contact-form').evaluate(form => form.requestSubmit());
    await page.waitForTimeout(400);
    check((await page.locator('.form-status').textContent()).includes('Too many requests.'), 'legacy 429 is not an email failure');
    check(await page.locator('#submit-lead').isEnabled(), 'unknown Retry-After does not lock UI indefinitely');
    check(await page.locator('a[href="mailto:business@aigrowlabs.media"]').count() === 1, 'correct public business email');
    check(!(await page.content()).includes('buisness@'), 'no business email typo');
    if (width < 681) {
      await jump(0);
      await page.locator('.menu').click();
      check(await page.locator('.menu').getAttribute('aria-expanded') === 'true', 'mobile menu opens');
      await page.locator('.nav-links a[href="#contact"]').click();
      check(await page.locator('.menu').getAttribute('aria-expanded') === 'false', 'mobile menu closes on navigation');
    }
    check(errors.length === 0, `no JS errors: ${errors.join('; ')}`);
    await context.close();
    console.log(`PASS ${width}px responsive/animation/navigation/form checks`);
  }
  // A browser without scroll timelines still uses a single passive RAF fallback.
  const page = await browser.newPage();
  await page.addInitScript(() => {
    const supports = CSS.supports.bind(CSS);
    CSS.supports = (...args) => args[0] === 'animation-timeline: scroll()' ? false : supports(...args);
  });
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate(() => scrollTo({ top: 1000, behavior: 'instant' }));
  await page.waitForTimeout(200);
  check(await page.locator('#scroll-progress').evaluate(e => e.style.transform.startsWith('scaleX(')), 'unsupported timeline fallback');
  console.log(`PASS ${checks} assertions; submissions mocked, no live emails sent.`);
} finally { await browser.close(); }
