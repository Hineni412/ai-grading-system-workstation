const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await p.goto('https://aihot.virxact.com/', { waitUntil: 'networkidle', timeout: 45000 });
  await p.screenshot({ path: 'prototypes/layout-refresh-20260811/_ref-aihot.png', fullPage: false });
  const info = await p.evaluate(() => {
    const out = {};
    const root = getComputedStyle(document.documentElement);
    const vars = [];
    for (const sheet of document.styleSheets) {
      try {
        for (const rule of sheet.cssRules) {
          if (rule.selectorText === ':root' || (rule.selectorText||'').includes(':root')) {
            for (const s of rule.style) if (s.startsWith('--')) vars.push(s + ':' + rule.style.getPropertyValue(s).trim());
          }
        }
      } catch (e) {}
    }
    out.rootVars = vars;
    const pick = (sel, props) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      const o = { tag: el.tagName, cls: (el.className||'').toString().slice(0,80), text: (el.textContent||'').trim().slice(0,30) };
      for (const pr of props) o[pr] = cs[pr];
      return o;
    };
    const props = ['color','backgroundColor','backgroundImage','borderRadius','border','boxShadow','fontFamily','fontSize','fontWeight','padding'];
    out.body = pick('body', props);
    const btns = [...document.querySelectorAll('button, a[role=button], .btn, [class*=button]')].slice(0, 8)
      .map(el => { const cs = getComputedStyle(el); return { cls: (el.className||'').toString().slice(0,60), text: (el.textContent||'').trim().slice(0,20), color: cs.color, bg: cs.backgroundColor, bgImg: cs.backgroundImage.slice(0,120), radius: cs.borderRadius, border: cs.border, shadow: cs.boxShadow.slice(0,120), fs: cs.fontSize, fw: cs.fontWeight, pad: cs.padding }; });
    out.buttons = btns;
    const headers = [...document.querySelectorAll('header, nav, [class*=header]')].slice(0,3)
      .map(el => { const cs = getComputedStyle(el); return { cls: (el.className||'').toString().slice(0,60), bg: cs.backgroundColor, bgImg: cs.backgroundImage.slice(0,150), border: cs.border, shadow: cs.boxShadow.slice(0,150), blur: cs.backdropFilter }; });
    out.headers = headers;
    const cards = [...document.querySelectorAll('article, [class*=card], [class*=item]')].slice(0,5)
      .map(el => { const cs = getComputedStyle(el); return { cls: (el.className||'').toString().slice(0,60), bg: cs.backgroundColor, radius: cs.borderRadius, border: cs.border, shadow: cs.boxShadow.slice(0,150) }; });
    out.cards = cards;
    const links = [...document.querySelectorAll('a')].slice(0,5).map(el => getComputedStyle(el).color);
    out.linkColors = links;
    return out;
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 6000));
  await b.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
