# Package: PiCast

**Type:** Code | **Source:** ~/Documents/Projects/Claude/terminal/picast/ | **Packaged:** 2026-02-19

---

## Essence

**The Word:** Relay

**The Sentence:** Turn your TV into a smart screen by controlling a Raspberry Pi media player from your phone, Telegram, or terminal.

**The Paragraph:**
PiCast transforms a Raspberry Pi connected to your TV into a media center you control remotely. Instead of using a TV remote or typing on a TV interface, you manage everything from your phone, computer, or Telegram. Queue up YouTube videos, control playback, adjust volume, and organize collections -- all while the Pi does the heavy lifting of playing content on your big screen. It's like having a smart TV, but you built the brains yourself.

---

## Marketing Copy

### Tagline
<!-- Under 60 characters, benefit-focused -->
Your TV, your phone, zero compromises.

### Description
PiCast turns any Raspberry Pi into a media center you control from anywhere. Install on your Pi, connect it to your TV via HDMI, then manage everything from your phone's web UI, Telegram, Mac terminal, or REST API. Queue YouTube videos, control playback, adjust volume, and organize collections -- all without touching a TV remote. It's a smart TV experience built on open-source hardware you actually own.

### Social Post
<!-- Under 280 characters -->
Turn your Raspberry Pi into a media center controlled from your phone. PiCast = YouTube queue player + universal remote, no TV interface required. Install on Pi, control from anywhere. Open source, zero subscription fees.

---

## Visual Pipeline

### Visual Concept
**Metaphor:** Control signal transmission -- command pulses radiating from a central transmitter node through geometric relay channels, splitting and branching as they propagate outward to multiple receiving endpoints
**Mood:** technical precision, clean digital aesthetic, sharp geometric forms, high contrast lighting (Code/Tool)

---

### Step 1: Hero (16:9) -- Midjourney v6.1

> This is the anchor image. Everything else derives from this.

**Prompt:**
```
technical engineering diagram showing control signal transmission network, left-anchored central transmitter node in amber geometric form, sharp angular relay channels radiating outward carrying command pulses, multiple receiving endpoints at varied distances, deep charcoal to soft violet gradient background, atmospheric depth particles on right side, muted teal circuit paths connecting nodes, precise technical line work, clean digital aesthetic, sharp contrast lighting, structured composition --ar 16:9 --style raw --stylize 500 --no text letters words writing
```

**Next:** Select your best hero output. Upload it in Step 2.

---

### Step 2: Logo (1:1) -- Midjourney Style Reference

> Upload your hero image as style reference via the MJ web UI image reference button.

**How to run:**
1. Open Midjourney web UI
2. Click the image reference button (image icon in prompt bar)
3. Upload your hero image from Step 1
4. Paste this prompt:

```
centered geometric relay transmitter symbol, angular hexagonal core in amber radiating signal lines outward, maximum 3 shapes total, rounded square container, deep charcoal background, muted teal accent lines, technical precision, clean geometric design, high contrast --ar 1:1 --style raw --stylize 400 --no text letters words writing
```

**Next:** Select your best logo output. Save as `assets/logo.png`.

---

### Step 3: Icon (1:1) -- Kling Restyle

> Upload your logo image from Step 2 into Kling Restyle mode.

**How to run:**
1. Go to [Kling AI](https://app.klingai.com/global/image-to-image/single/new) > Image to Image
2. Choose **Restyle** (not Single Reference)
3. Upload your logo image from Step 2
4. Paste this prompt:

```
ultra-simplified relay transmitter icon, single amber hexagonal center with minimal radiating signal lines, only 2 geometric shapes maximum, deep charcoal background, clean technical symbol, high contrast design, legible at tiny sizes
```

5. Save as `assets/icon.png`

**Verify:** Does this read at 32x32px?

---

## Ecosystem Style Applied

| Check | Status |
|-------|--------|
| Charcoal ground (60%+) | ✓ |
| Amber focal point | ✓ |
| Hero -> Logo -> Icon reference chain | ✓ |
| Step 2 includes MJ style reference instructions | ✓ |
| Step 3 includes Kling Restyle instructions | ✓ |
| No channel brand color dominance | ✓ |
| Logo readable at 64px | ✓ |
| Icon legible at 32px | ✓ |
| All prompts have "no text" | ✓ |
| Hero left-anchored with right atmospheric depth | ✓ |
| Mood keywords match project type | ✓ |
| Logo max 3 shapes, icon max 2 shapes | ✓ |
| Same metaphor x3 zoom levels | ✓ |
