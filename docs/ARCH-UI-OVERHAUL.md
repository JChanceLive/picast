# Architecture: PiCast Mobile UI Overhaul

**Date:** 2026-03-12
**Status:** SESSION 2 COMPLETE + DEPLOYED (v1.1.0a19). Session 3 next: nav bar + status bars + cross-page consistency
**Scope:** Full visual redesign of PiCast web UI (player.html, style.css, base.html)
**Target:** iPhone SE (320px) through iPhone 16 (430px), OLED-optimized dark theme

---

## Design Principles

1. **320px-first** — Everything fits on iPhone SE without horizontal scrolling
2. **Thumb zone** — Primary controls in bottom 60% of screen
3. **Progressive disclosure** — Show 5-6 primary controls, collapse rest into overflow
4. **Zero-UI gestures** — Swipe actions complement (not replace) taps
5. **Information density** — Show what matters, hide what doesn't (title > URL)
6. **OLED-optimized** — True black backgrounds, minimal burn-in risk, battery efficient

---

## Current Problems

| Problem | Current State | Impact |
|---------|--------------|--------|
| Controls overflow | 10 buttons in single row, requires scroll | Users miss buttons, no scroll indicator |
| Tiny text | 0.55rem labels, 0.8rem URLs | Unreadable on small screens |
| Now Playing URL | Shows full `watch?v=ID` | Wastes space, no value to user |
| No padding | 0.15-0.3rem gaps | Cramped, hard to tap accurately |
| Status prefix | `>> ` or `|| ` prepended to title | Ugly, wastes chars on small screen |
| Thumbnail on small screen | 120x68px thumb + text crammed beside it | Layout breaks, text gets 1 line |
| Volume/speed always visible | `Vol: 100 Speed: 1.0x` row | Rarely needed info taking space |

---

## Design System

### Color Palette (OKLCH-based)

Replace the current neon cyber theme with a refined dark palette:

```css
:root {
    /* Backgrounds — OLED layered elevation */
    --bg-0: #000000;           /* True black — OLED base */
    --bg-1: #0d0d0f;           /* Elevated surface */
    --bg-2: #1a1a1f;           /* Cards, modals */
    --bg-3: #252530;           /* Hover states, active cards */

    /* Text hierarchy */
    --text-primary: #f0f0f5;   /* Titles, primary content */
    --text-secondary: #8e8e9a; /* Subtitles, metadata */
    --text-tertiary: #4a4a55;  /* Timestamps, IDs, hints */

    /* Accent — single vibrant color, used sparingly */
    --accent: oklch(72% 0.19 250);       /* Electric blue */
    --accent-dim: oklch(72% 0.19 250 / 0.15);
    --accent-glow: oklch(72% 0.19 250 / 0.3);

    /* Semantic */
    --danger: oklch(65% 0.22 25);        /* Warm red */
    --success: oklch(75% 0.18 145);      /* Soft green */
    --warning: oklch(78% 0.16 85);       /* Amber */

    /* Borders — subtle, barely there */
    --border: rgba(255, 255, 255, 0.06);
    --border-hover: rgba(255, 255, 255, 0.12);

    /* Spacing scale (4px base) */
    --sp-1: 4px;
    --sp-2: 8px;
    --sp-3: 12px;
    --sp-4: 16px;
    --sp-5: 20px;
    --sp-6: 24px;
    --sp-8: 32px;

    /* Radius */
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --radius-full: 9999px;

    /* Touch targets */
    --touch-min: 44px;
    --touch-comfortable: 48px;
}
```

### Typography

```css
body {
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text',
                 system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
}

/* Scale */
--font-xs: clamp(11px, 3vw, 12px);    /* IDs, timestamps */
--font-sm: clamp(13px, 3.5vw, 14px);  /* Metadata, labels */
--font-md: clamp(15px, 4vw, 16px);    /* Body, queue items */
--font-lg: clamp(17px, 4.5vw, 20px);  /* Now Playing title */
--font-xl: clamp(20px, 5.5vw, 28px);  /* Hero / idle state */
```

### Touch Targets

- **Primary actions** (play/pause, skip): 48x48px minimum
- **Secondary actions** (loop, speed, timers): 44x44px minimum
- **Tertiary actions** (remove, move): 36x36px minimum (in context menus)
- **Gap between targets**: minimum 8px

---

## Component Redesigns

### 1. Now Playing Card

**Before:** Title with `>> ` prefix, full URL below, thumbnail + text side-by-side, vol/speed always visible.

**After:**

```
┌─────────────────────────────────┐
│ [THUMB 80x45]  Title Here       │
│                Artist / Source   │
│ ████████████░░░░░  2:34 / 4:12  │
│                                 │
│  [Paused/Playing indicator]     │
└─────────────────────────────────┘
```

**Changes:**
- Remove `>> ` / `|| ` prefix — use a small pulsing dot or play/pause icon inline
- **Title**: Left-aligned, `--font-lg`, `font-weight: 600`, single line with `text-overflow: ellipsis`
- **Subtitle**: Source type tag (YT / Twitch / Archive) + video ID in `--text-tertiary`, right-aligned
- **URL**: Hidden entirely — no user value. Video ID shown small if needed for debugging
- **Thumbnail**: Smaller (80x45px) on <380px, hidden on <340px. Full-width hero option for idle
- **Vol/Speed**: Hidden by default. Visible only when user taps a "..." overflow or adjusts volume
- **Progress bar**: Full-width, thicker (10px), with larger hit area (24px) for easy scrubbing
- **Time**: Right of progress bar, `--font-xs`
- **Livestream handling**: If `duration === 0` or source is Twitch, show "LIVE" badge instead of progress bar. Red dot + "LIVE" text, no time display

### 2. Controls Bar — Two-Tier Layout

**Before:** 10 buttons in one scrollable row (Pause, Skip, Stop, Slow, Fast, 1More, 30m, 60m, Loop, Multi).

**After:** Split into primary row (always visible) + overflow drawer:

```
Primary Row (always visible, centered):
┌─────────────────────────────────┐
│   ⏹   ⏸    ⏭    🔁   📺      │
│  Stop Pause Skip  Loop Multi    │
└─────────────────────────────────┘

Overflow (tap "•••" to expand/collapse):
┌─────────────────────────────────┐
│  🐌 Slow  🐇 Fast  ⏱ 1More    │
│  😴 30m   😴 60m               │
└─────────────────────────────────┘
```

**Primary controls (5):** Stop, Pause/Play, Skip, Loop, Multi-TV
**Overflow controls (5):** Slow, Fast, 1More (stop-after), 30m Sleep, 60m Sleep

**Layout CSS:**
```css
.controls-primary {
    display: flex;
    justify-content: center;
    gap: clamp(8px, 3vw, 16px);
    padding: var(--sp-3) var(--sp-4);
}

.controls-overflow {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(64px, 1fr));
    gap: var(--sp-2);
    padding: var(--sp-3) var(--sp-4);
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.controls-overflow.expanded {
    max-height: 120px;
}
```

**Button design:**
```css
.ctrl-btn {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    width: var(--touch-comfortable);
    height: var(--touch-comfortable);
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: var(--bg-2);
    color: var(--text-primary);
    gap: 2px;
    transition: transform 0.1s, background 0.15s;
}

.ctrl-btn:active {
    transform: scale(0.93);
    background: var(--bg-3);
}

.ctrl-btn.active {
    background: var(--accent);
    color: var(--bg-0);
    border-color: var(--accent);
}

.ctrl-icon { font-size: 1.2rem; }
.ctrl-label { font-size: var(--font-xs); color: var(--text-secondary); }
```

**Overflow toggle:** A subtle `•••` button at the end of the primary row, or below it. Tapping expands the second row with a slide-down animation.

### 3. Queue Items

**Before:** Complex row with play button, status icon, thumb, source tag, title, URL, video ID, watch count, actions — all crammed.

**After:** Clean card layout:

```
┌──────────────────────────────────────┐
│ ▶  [thumb]  Title of Video Here      │
│             YT • 3x plays     ╳  ↕  │
└──────────────────────────────────────┘
```

**Changes:**
- **URL row**: Remove entirely. Source tag + play count is enough metadata
- **Video ID**: Hidden on small screens (already done at <480px), show on hover/long-press on desktop
- **Status**: Use left border color instead of icon (cyan=playing, green=pending, dim=played, red=failed)
- **Actions**: Collapsed by default. Swipe-left reveals remove/requeue/add-to-collection. Or: tap to expand inline action row
- **Played items**: Dimmed to 50% opacity, collapsed height. Clear visual distinction from pending
- **Failed items**: Red left border, error text below title in `--font-xs`

### 4. Queue Actions (Bottom Bar)

**Before:** "Clear Played" + "Clear All" + "Refresh Queue" as text buttons.

**After:** Pill-shaped action bar:

```
┌─────────────────────────────────┐
│ [Refresh] [Clear Played] [Clear]│
└─────────────────────────────────┘
```

- Use outlined pill buttons with icons
- "Refresh" = primary style (accent border)
- "Clear All" = danger style (red text, requires double-tap to confirm)

### 5. Navigation Bar

**Before:** Brand text + 4 icon buttons + hamburger, all in one row.

**After:**
- Keep the current pattern but increase touch targets to 40px minimum
- Active page indicator: Bottom border accent line (like iOS tab bar)
- Hide brand text on <360px, show icon only
- Keep hamburger menu for secondary pages

### 6. Status Bars (Timer, Multi-TV)

**Before:** Text-only status below controls.

**After:**
- Integrate into the controls section as a subtle animated banner
- Multi-TV: Show device count + queue remaining as a compact chip: `2/2 TVs • 5 remaining`
- Sleep timer: Countdown as a progress ring around the sleep button itself
- Use `backdrop-filter: blur(8px)` for a frosted glass effect

### 7. Volume Control

**Before:** Slider always visible in controls section.

**After:**
- Move to a slide-out panel triggered by a volume icon in nav or long-press
- Or: vertical slider overlay on the right edge (like iOS volume HUD)
- Current volume shown as a small badge on the volume icon

---

## Microinteractions

### Press Feedback
```css
.ctrl-btn:active {
    animation: press 0.15s cubic-bezier(0.68, -0.55, 0.265, 1.55);
}

@keyframes press {
    0% { transform: scale(1); }
    40% { transform: scale(0.92); }
    100% { transform: scale(1); }
}
```

### State Transitions
- Playing → Paused: Smooth icon morph (CSS clip-path or SVG)
- Item added to queue: Slide-in from right with fade
- Item removed: Slide-out left with fade + height collapse
- Multi-TV enabled: Button pulses once, status fades in

### Progress Bar
- Larger invisible hit area (24px height, 8px visible)
- On touch: Expand to 12px visible with timestamp tooltip
- Smooth `transition: width 1s linear` between poll updates

---

## Implementation Plan

### Session 1: Foundation + Controls (Est. ~600 lines changed)
1. Replace CSS variables (color palette, spacing, typography)
2. Redesign controls bar (primary + overflow two-tier)
3. Fix Now Playing card (remove URL, fix title, add live badge)
4. Add press feedback microinteractions

### Session 2: Queue + Polish (Est. ~500 lines changed)
1. Redesign queue items (cleaner cards, status borders, dimmed played)
2. Redesign queue actions (pill buttons)
3. Add overflow toggle animation
4. Volume control relocation
5. Responsive breakpoints tuning (320px, 380px, 480px)
6. Test on iPhone SE + iPhone 16 viewports

### Session 3: Navigation + Status + Final (Est. ~300 lines changed)
1. Navigation bar refresh
2. Status bars redesign (chips, progress rings)
3. Transitions and animations polish
4. Cross-page consistency (base.html, other templates)
5. Full test pass on all pages

---

## Breakpoints

| Width | Target Devices | Adjustments |
|-------|---------------|-------------|
| ≤340px | iPhone SE, iPhone 5 | Hide thumbnails, icon-only nav, compact controls |
| 341-390px | iPhone 12/13 mini | Show small thumbnails, full controls |
| 391-430px | iPhone 14-16 | Comfortable spacing, full layout |
| 431-768px | iPad, landscape | Two-column queue, expanded controls |
| ≥769px | Desktop | Sidebar queue, full now-playing hero |

---

## Files Changed

| File | Changes |
|------|---------|
| `src/picast/server/static/style.css` | Full rewrite of color system, controls, now-playing, queue items, status bars, breakpoints (~800 lines touched) |
| `src/picast/server/templates/player.html` | Restructure controls HTML (two-tier), simplify now-playing, redesign queue rendering JS, add overflow toggle |
| `src/picast/server/templates/base.html` | Nav bar updates, touch target sizing |
| `src/picast/server/templates/player.html` (JS) | Fix `updateNowPlaying()` — remove `>> ` prefix, smart title display, live detection, overflow toggle state |

---

## Constraints

- **No build tools** — Pure CSS, no preprocessors. All styles in one `style.css`
- **No JS frameworks** — Vanilla JS, inline in templates
- **Flask templates** — Jinja2 templating, server-rendered HTML
- **Pi-hosted** — Assets served from Raspberry Pi, minimize file sizes
- **PWA support** — Must work as home-screen app on iOS Safari
- **Backwards compatible** — All API endpoints unchanged, only visual layer

---

## Testing Checklist

- [ ] iPhone SE (320px) — all controls visible without scrolling
- [ ] iPhone 16 (430px) — comfortable spacing, no wasted space
- [ ] Landscape mode — controls don't break
- [ ] Now Playing: YouTube video shows title, not URL
- [ ] Now Playing: Twitch stream shows "LIVE" badge
- [ ] Now Playing: No title available shows video ID fallback
- [ ] Controls: Primary 5 buttons always visible
- [ ] Controls: Overflow expands/collapses smoothly
- [ ] Queue: Played items visually dimmed
- [ ] Queue: Failed items show error with red border
- [ ] Multi-TV status shows as compact chip
- [ ] Sleep timer countdown visible
- [ ] Volume slider accessible
- [ ] All pages (history, settings, catalog, collections) still render correctly
