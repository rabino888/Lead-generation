"""
Record silent marketing walkthroughs of the Lead List Ledger portal.

Showcase clients: Automata + TBOmedia. Other clients are hidden; prospect
company / person / email / URL fields are blurred for privacy.

Requires the dashboard server (scripts/serve_cost_dashboard.py) and Playwright.

Usage:
  python scripts/record_marketing_videos.py
  python scripts/record_marketing_videos.py --base http://127.0.0.1:8765 --out marketing/videos
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FFMPEG = (
    shutil.which("ffmpeg")
    or r"C:\Users\ravi_\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe"
)

# Public showcase brands (TBOmedia family). Everything else is hidden or blurred.
SHOWCASE_CLIENTS = ("automata", "tbomedia")
SHOWCASE_CAMPAIGN = "automata_us_rnd"

PRIVACY_INIT_JS = r"""
(() => {
  if (window.__mktPrivacyInstalled) return;
  window.__mktPrivacyInstalled = true;

  const ALLOWED = new Set(%ALLOWED%);

  const style = document.createElement('style');
  style.id = 'mkt-privacy-css';
  style.textContent = `
    .mkt-blur {
      filter: blur(8px) !important;
      user-select: none !important;
      pointer-events: none !important;
    }
    .mkt-hide { display: none !important; }
  `;
  (document.head || document.documentElement).appendChild(style);

  function norm(s) {
    return String(s || '').trim().toLowerCase();
  }

  function isAllowedClient(text) {
    const t = norm(text);
    if (!t) return false;
    for (const a of ALLOWED) {
      if (t === a || t.includes(a)) return true;
    }
    return false;
  }

  function isShowcaseCampaignId(id) {
    const t = norm(id);
    return [...ALLOWED].some((a) => t === a || t.startsWith(a + '_') || t.startsWith(a));
  }

  function blurNode(node) {
    if (!node || node.classList.contains('mkt-blur')) return;
    node.classList.add('mkt-blur');
  }

  function hideRow(node) {
    if (!node) return;
    node.classList.add('mkt-hide');
  }

  function headerCells(table) {
    const thead = [...table.querySelectorAll('thead th, thead td')];
    if (thead.length) return thead.map((h) => norm(h.textContent));
    const first = table.querySelector('tr');
    if (!first) return [];
    return [...first.children].map((h) => norm(h.textContent));
  }

  function dataRows(table) {
    const bodyRows = [...table.querySelectorAll('tbody tr')];
    if (bodyRows.length) return bodyRows;
    return [...table.querySelectorAll('tr')].slice(1);
  }

  function scrubTables() {
    document.querySelectorAll('table').forEach((table) => {
      const headers = headerCells(table);
      const clientCol = headers.findIndex(
        (h) => h === 'client' || h.startsWith('client ')
      );
      const isClientScoped = clientCol >= 0;
      const isLeadish = headers.some((h) =>
        h.includes('company')
        || h.includes('email')
        || h.includes('linkedin')
        || h.includes('decision')
        || h.includes('website')
        || h.includes('domain')
      );

      dataRows(table).forEach((tr) => {
        const cells = [...tr.children];
        if (!cells.length) return;

        if (isClientScoped) {
          const clientText = (cells[clientCol] && cells[clientCol].textContent) || '';
          if (clientText && !isAllowedClient(clientText)) {
            hideRow(tr);
            return;
          }
        }

        if (isLeadish) {
          headers.forEach((h, i) => {
            if (!cells[i]) return;
            if (
              h.includes('company')
              || h.includes('website')
              || h.includes('domain')
              || h.includes('email')
              || h.includes('linkedin')
              || h.includes('name')
              || h.includes('title')
              || h.includes('decision')
              || h.includes('contact')
              || h.includes('location')
            ) {
              blurNode(cells[i]);
            }
          });
        }
      });
    });
  }

  function scrubClientLinks() {
    document.querySelectorAll('a[href*="#/client/"]').forEach((a) => {
      const href = a.getAttribute('href') || '';
      const text = a.textContent || '';
      const id = decodeURIComponent((href.split('#/client/')[1] || '').split(/[/?#]/)[0] || '');
      if (!isAllowedClient(id) && !isAllowedClient(text)) {
        hideRow(a.closest('tr, .list-row, li') || a);
      }
    });
    document.querySelectorAll('a[href*="#/campaign/"]').forEach((a) => {
      const href = a.getAttribute('href') || '';
      const id = decodeURIComponent((href.split('#/campaign/')[1] || '').split(/[/?#]/)[0] || '');
      if (id && !isShowcaseCampaignId(id)) {
        hideRow(a.closest('tr, .list-row, li') || a);
      }
    });
  }

  function scrubProspectText() {
    document.querySelectorAll('.seed-list li').forEach(blurNode);
    document.querySelectorAll('td, .seed-list li, .list-row').forEach((el) => {
      if (el.classList.contains('mkt-hide') || el.classList.contains('mkt-blur')) return;
      const t = el.textContent || '';
      if (/@[\w.-]+\.\w+|https?:\/\/|linkedin\.com\/in\//i.test(t)) {
        blurNode(el);
      }
    });
  }

  function scrubFilters() {
    document.querySelectorAll('select').forEach((sel) => {
      [...sel.options].forEach((opt) => {
        const v = norm(opt.value || opt.textContent);
        if (!v || v === 'all') return;
        if (!isAllowedClient(v) && !isAllowedClient(opt.textContent)) {
          opt.hidden = true;
          opt.disabled = true;
        }
      });
    });
  }

  function scrub() {
    try {
      scrubTables();
      scrubClientLinks();
      scrubProspectText();
      scrubFilters();
    } catch (e) {
      console.warn('mkt privacy scrub', e);
    }
  }

  window.__mktPrivacyScrub = scrub;

  const mo = new MutationObserver(() => {
    clearTimeout(window.__mktPrivacyTimer);
    window.__mktPrivacyTimer = setTimeout(scrub, 60);
  });

  function start() {
    scrub();
    if (document.body) {
      mo.observe(document.body, { childList: true, subtree: true });
    }
    // Ledger paints after /dashboard/api — keep scrubbing briefly
    let n = 0;
    const tick = setInterval(() => {
      scrub();
      if (++n > 40) clearInterval(tick);
    }, 250);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
""".replace("%ALLOWED%", repr(list(SHOWCASE_CLIENTS)))


def _wait(page, ms: int) -> None:
    page.wait_for_timeout(ms)


def _scrub(page) -> None:
    """Install privacy helpers if missing, then scrub the current DOM."""
    page.evaluate(
        """(src) => {
          if (!window.__mktPrivacyScrub) {
            // eslint-disable-next-line no-eval
            (0, eval)(src);
          }
          if (window.__mktPrivacyScrub) window.__mktPrivacyScrub();
        }""",
        PRIVACY_INIT_JS,
    )
    _wait(page, 250)


def _smooth_scroll(page, total_px: int = 1400, step: int = 120, pause_ms: int = 180) -> None:
    scrolled = 0
    while scrolled < total_px:
        page.mouse.wheel(0, step)
        scrolled += step
        _wait(page, pause_ms)


def _to_mp4(webm: Path, mp4: Path) -> None:
    if not FFMPEG or not Path(FFMPEG).is_file():
        print(f"ffmpeg not found — leaving {webm.name}", file=sys.stderr)
        return
    mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG,
        "-y",
        "-i",
        str(webm),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(mp4),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"Wrote {mp4}")


def _record_clip(browser, out_dir: Path, name: str, base: str, run) -> Path:
    video_dir = out_dir / "_raw" / name
    if video_dir.exists():
        shutil.rmtree(video_dir)
    video_dir.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(video_dir),
        record_video_size={"width": 1440, "height": 900},
        device_scale_factor=1,
    )
    page = context.new_page()
    try:
        run(page, base)
    finally:
        page.close()
        context.close()
    raw = next(video_dir.glob("*.webm"))
    dest_webm = out_dir / f"{name}.webm"
    shutil.copy2(raw, dest_webm)
    _to_mp4(dest_webm, out_dir / f"{name}.mp4")
    return dest_webm


def clip_overview(page, base: str) -> None:
    """Ledger home → Automata client → TBOmedia client."""
    page.goto(f"{base}/dashboard", wait_until="networkidle")
    _wait(page, 2500)
    _scrub(page)
    _smooth_scroll(page, 700)
    _scrub(page)
    _wait(page, 900)
    page.mouse.wheel(0, -700)
    _wait(page, 600)
    page.locator('a:has-text("Automata")').first.click()
    page.wait_for_url("**/dashboard#/client/**")
    _wait(page, 1800)
    _scrub(page)
    _smooth_scroll(page, 800)
    _wait(page, 1000)
    page.goto(f"{base}/dashboard", wait_until="networkidle")
    _wait(page, 1800)
    _scrub(page)
    page.locator('table a:has-text("Tbomedia"), table a:has-text("TBOmedia")').first.click()
    page.wait_for_url("**/dashboard#/client/**")
    _wait(page, 1800)
    _scrub(page)
    _smooth_scroll(page, 600)
    _wait(page, 1200)


def clip_campaign(page, base: str) -> None:
    """Automata campaign detail with prospect fields blurred."""
    page.goto(
        f"{base}/dashboard#/campaign/{SHOWCASE_CAMPAIGN}",
        wait_until="networkidle",
    )
    _wait(page, 2500)
    _scrub(page)
    _smooth_scroll(page, 1700, step=140, pause_ms=200)
    _scrub(page)
    _wait(page, 1400)
    page.mouse.wheel(0, -900)
    _wait(page, 800)
    full = page.locator('a:has-text("Full results")')
    if full.count():
        full.first.click()
        _wait(page, 2400)
        _scrub(page)
        _smooth_scroll(page, 600)
        _wait(page, 1400)


def clip_builder(page, base: str) -> None:
    """Builder create for Automata, then enrichment board for showcase campaign."""
    page.goto(f"{base}/builder?new=1&client=automata", wait_until="networkidle")
    _wait(page, 2200)
    _scrub(page)
    _smooth_scroll(page, 900)
    _wait(page, 1200)
    page.goto(
        f"{base}/builder?campaign={SHOWCASE_CAMPAIGN}",
        wait_until="networkidle",
    )
    _wait(page, 2600)
    _scrub(page)
    _smooth_scroll(page, 1100)
    _wait(page, 1600)
    confirm = page.locator(
        'button:has-text("Continue to confirm"), a:has-text("Continue to confirm")'
    )
    if confirm.count():
        confirm.first.click()
        _wait(page, 2000)
        _scrub(page)
        _smooth_scroll(page, 600)
        _wait(page, 1400)


def main() -> int:
    parser = argparse.ArgumentParser(description="Record silent marketing portal videos")
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--out",
        default=str(ROOT / "marketing" / "videos"),
        help="Output directory for webm/mp4 clips",
    )
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    clips = [
        ("01_ledger_overview", clip_overview),
        ("02_campaign_detail", clip_campaign),
        ("03_campaign_builder", clip_builder),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for name, fn in clips:
                print(f"Recording {name}…")
                _record_clip(browser, out_dir, name, args.base, fn)
        finally:
            browser.close()

    print(f"Done. Showcase clients: {', '.join(SHOWCASE_CLIENTS)}")
    print(f"Clips in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
