// Deterministic before/after screenshot comparison at the SAME animation phases.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tools/compare-visuals.mjs BEFORE_URL AFTER_URL OUTPUT_DIR
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const [before, after, output = '/tmp/aigrow-visuals'] = process.argv.slice(2);
if (!before || !after) throw new Error('Supply before and after URLs');
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome' });
const results = [];
try {
  for (const width of [1440, 768, 393]) {
    const screenshots = {};
    for (const [label, url] of [['before', before], ['after', after]]) {
      const page = await browser.newPage({ viewport: { width, height: 851 }, deviceScaleFactor: 1,
        isMobile: width < 901, hasTouch: width < 901 });
      await page.goto(url, { waitUntil: 'networkidle' });
      await page.evaluate(() => document.fonts.ready);
      await page.addStyleTag({ content: `
        *, *::before, *::after { animation-play-state: paused !important; transition: none !important; }
        [data-r] { opacity: 1 !important; transform: none !important; will-change: auto !important; }
        .preloader, .scroll-progress { display: none !important; }
      ` });
      for (const [region, phase] of [['creator', 2000], ['contact', 0], ['contact', 2000], ['contact', 4000]]) {
        await page.evaluate(({ region, phase }) => {
          const element = document.querySelector(region === 'creator' ? '.framework' : '.contact');
          scrollTo({ top: element.getBoundingClientRect().top + scrollY, behavior: 'instant' });
          document.getAnimations().forEach(animation => {
            if (animation.timeline instanceof DocumentTimeline) {
              animation.pause(); animation.currentTime = phase;
            }
          });
        }, { region, phase });
        await page.waitForTimeout(200);
        const key = `${width}-${region}-${phase}`;
        const image = await page.screenshot({ path: path.join(output, `${label}-${key}.png`) });
        if (label === 'before') screenshots[key] = image.toString('base64');
        else {
          const difference = await page.evaluate(async ([a, b]) => {
            const pixels = async encoded => {
              const image = new Image(); image.src = 'data:image/png;base64,' + encoded;
              await image.decode();
              const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
              const ctx = canvas.getContext('2d', { willReadFrequently: true }); ctx.drawImage(image, 0, 0);
              return ctx.getImageData(0, 0, canvas.width, canvas.height).data;
            };
            const [left, right] = await Promise.all([pixels(a), pixels(b)]);
            let changedPixels = 0, absoluteDifference = 0, maxChannelDifference = 0;
            for (let i = 0; i < left.length; i += 4) {
              let changed = false;
              for (let channel = 0; channel < 3; channel++) {
                const difference = Math.abs(left[i + channel] - right[i + channel]);
                changed ||= difference > 0;
                absoluteDifference += difference;
                maxChannelDifference = Math.max(maxChannelDifference, difference);
              }
              if (changed) changedPixels++;
            }
            return { changedPixels, totalPixels: left.length / 4, maxChannelDifference,
              meanChannelDifference: absoluteDifference / (left.length / 4 * 3) };
          }, [screenshots[key], image.toString('base64')]);
          results.push({ region, width, phase, ...difference });
          console.log(JSON.stringify(results.at(-1)));
        }
      }
      await page.close();
    }
  }
} finally {
  await fs.writeFile(path.join(output, 'comparison.json'), JSON.stringify(results, null, 2));
  await browser.close();
}
