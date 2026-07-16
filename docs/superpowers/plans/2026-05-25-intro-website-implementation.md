# Intro Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a two-page Astro intro website (landing + docs) for the Assistant OSS project.

**Architecture:** Astro static site generation. Each visual section is a standalone `.astro` component under `src/components/`. The landing page (`index.astro`) composes them in order. Design tokens are CSS custom properties in `src/styles/tokens.css`. No framework dependencies (React/Vue/Svelte).

**Tech Stack:** Astro 5, CSS custom properties (no Tailwind), Inter + Fira Code via Google Fonts.

**Spec:** `docs/superpowers/specs/2026-05-25-intro-website-design.md`

---

## File Structure

```
site/
├── public/
│   └── favicon.svg                          # Connected logo as favicon
├── src/
│   ├── styles/
│   │   └── tokens.css                       # Design tokens (already exists)
│   ├── components/
│   │   ├── LogoConnected.astro              # Inline SVG for the Connected logo
│   │   ├── Nav.astro                        # Top navigation bar
│   │   ├── Hero.astro                       # Hero section
│   │   ├── Features.astro                   # 2×2 feature grid
│   │   ├── Differentiation.astro            # 4 pillar comparison cards
│   │   ├── Trust.astro                      # 3 trust cards
│   │   ├── CTA.astro                        # Call-to-action section
│   │   └── Footer.astro                     # 4-column footer
│   ├── layouts/
│   │   └── LandingLayout.astro              # Base layout (exists, may need polish)
│   └── pages/
│       ├── index.astro                      # Landing page composition
│       └── docs.astro                       # Docs page skeleton
```

No existing files need modification except `src/layouts/LandingLayout.astro` (add Fira Code) and `src/pages/index.astro` (replace placeholder content).

---

### Task 1: Create Connected Logo SVG + Favicon

**Files:**
- Create: `src/components/LogoConnected.astro`
- Create: `public/favicon.svg`

- [ ] **Step 1: Create LogoConnected.astro**

```astro
---
export interface Props {
  size?: number;
  class?: string;
}

const { size = 24 } = Astro.props;
---

<svg
  width={size}
  height={size}
  viewBox="0 0 56 56"
  fill="none"
  class={Astro.props.class}
  role="img"
  aria-label="Assistant logo"
>
  <circle cx="20" cy="28" r="8" fill="var(--ea-accent, #239766)" />
  <circle cx="36" cy="28" r="5" fill="none" stroke="var(--ea-accent, #239766)" stroke-width="2.5" />
  <line x1="28" y1="28" x2="31" y2="28" stroke="var(--ea-accent, #239766)" stroke-width="2.5" stroke-linecap="round" />
</svg>
```

- [ ] **Step 2: Create favicon.svg**

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="12 8 32 40" fill="none">
  <circle cx="24" cy="28" r="8" fill="#239766"/>
  <circle cx="38" cy="28" r="5" fill="none" stroke="#239766" stroke-width="2.5"/>
  <line x1="32" y1="28" x2="33" y2="28" stroke="#239766" stroke-width="2.5" stroke-linecap="round"/>
</svg>
```

Write to `public/favicon.svg`. This is a cropped viewBox showing just the two dots (not the full 56×56 canvas) so it's readable at favicon size.

- [ ] **Step 3: Update astro.config.mjs to reference the favicon**

Read `site/astro.config.mjs` and ensure it has no favicon config that conflicts. Default Astro minimal template should work fine.

---

### Task 2: Build Nav Component

**Files:**
- Create: `src/components/Nav.astro`

- [ ] **Step 1: Create Nav.astro**

```astro
---
import LogoConnected from "./LogoConnected.astro";
---

<nav class="nav">
  <a href="/" class="nav-brand">
    <LogoConnected size={28} />
    <span class="nav-wordmark">Assistant</span>
  </a>
  <div class="nav-links">
    <a href="#features" class="nav-link">Features</a>
    <a href="/docs" class="nav-link">Docs</a>
    <a href="https://github.com" class="nav-link">GitHub</a>
    <a href="#cta" class="nav-cta">Get Started</a>
  </div>
</nav>

<style>
  .nav {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 32px;
    border-bottom: 1px solid var(--ea-border-subtle);
    max-width: 900px;
    margin: 0 auto;
  }
  .nav-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    text-decoration: none;
  }
  .nav-wordmark {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.012em;
    color: var(--ea-text-primary);
  }
  .nav-links {
    display: flex;
    align-items: center;
    gap: 24px;
  }
  .nav-link {
    font-size: 13px;
    color: var(--ea-text-secondary);
    text-decoration: none;
    letter-spacing: -0.005em;
    transition: color 0.15s;
  }
  .nav-link:hover {
    color: var(--ea-text-primary);
  }
  .nav-cta {
    background: var(--ea-accent);
    color: var(--ea-text-inverse);
    padding: 7px 16px;
    border-radius: var(--ea-radius-sm);
    font-size: 13px;
    font-weight: 500;
    text-decoration: none;
    letter-spacing: -0.005em;
  }
</style>
```

---

### Task 3: Build Hero Component

**Files:**
- Create: `src/components/Hero.astro`

- [ ] **Step 1: Create Hero.astro**

```astro
---
import LogoConnected from "./LogoConnected.astro";
---

<section class="hero">
  <span class="hero-tag">Open Source · Self-Hosted</span>
  <h1 class="hero-title">
    Your assistant.<br />
    <span class="hero-em">One that gets you.</span>
  </h1>
  <p class="hero-sub">
    Learns your preferences, your workflow, what matters to you.
    Gets smarter every conversation — while your data stays on your machine.
  </p>
  <div class="hero-buttons">
    <a href="#" class="btn-primary">Download for macOS</a>
    <a href="https://github.com" class="btn-secondary">View on GitHub</a>
  </div>
  <div class="hero-screenshot">
    <div class="hero-screenshot-placeholder">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.4">
        <rect x="2" y="3" width="20" height="18" rx="2" />
        <line x1="6" y1="7" x2="18" y2="7" />
        <line x1="6" y1="11" x2="18" y2="11" />
        <line x1="6" y1="15" x2="14" y2="15" />
      </svg>
      <span>App screenshot — the Flutter chat interface</span>
    </div>
  </div>
</section>

<style>
  .hero {
    text-align: center;
    padding: 72px 32px 56px;
    max-width: 680px;
    margin: 0 auto;
  }
  .hero-tag {
    display: inline-block;
    background: var(--ea-accent-muted);
    color: var(--ea-success);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 4px 12px;
    border-radius: 999px;
    margin-bottom: 20px;
  }
  .hero-title {
    font-size: 40px;
    font-weight: 600;
    letter-spacing: -0.02em;
    line-height: 1.15;
    margin: 0 0 12px;
    color: var(--ea-text-primary);
  }
  .hero-em {
    color: var(--ea-accent);
  }
  .hero-sub {
    font-size: 15px;
    line-height: 1.6;
    color: var(--ea-text-secondary);
    letter-spacing: -0.011em;
    margin: 0 auto 28px;
    max-width: 520px;
  }
  .hero-buttons {
    display: flex;
    justify-content: center;
    gap: 10px;
  }
  .btn-primary {
    background: var(--ea-accent);
    color: var(--ea-text-inverse);
    padding: 11px 24px;
    border-radius: var(--ea-radius-sm);
    font-weight: 500;
    font-size: 14px;
    text-decoration: none;
    display: inline-block;
  }
  .btn-secondary {
    background: transparent;
    color: var(--ea-text-primary);
    padding: 11px 24px;
    border-radius: var(--ea-radius-sm);
    border: 1px solid var(--ea-border-default);
    font-size: 14px;
    text-decoration: none;
    display: inline-block;
  }
  .hero-screenshot {
    margin-top: 48px;
    background: var(--ea-bg-surface);
    border: 1px solid var(--ea-border-subtle);
    border-radius: var(--ea-radius-lg);
    padding: 40px;
  }
  .hero-screenshot-placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    color: var(--ea-text-tertiary);
    font-size: 13px;
    letter-spacing: -0.005em;
  }
</style>
```

---

### Task 4: Build Features Component (2×2 Grid)

**Files:**
- Create: `src/components/Features.astro`

- [ ] **Step 1: Create Features.astro**

```astro
---

const features = [
  {
    icon: "✉️",
    title: "Manages your email",
    desc: "Connect Gmail, Outlook, or iCloud. Read, search, reply, send — all through conversation.",
  },
  {
    icon: "🌐",
    title: "Browses the web",
    desc: "Opens pages, fills forms, logs into sites, extracts data. It does things, not just chat.",
  },
  {
    icon: "🧠",
    title: "Remembers everything",
    desc: "Learns your preferences, your workflows, your corrections. Gets better every conversation.",
  },
  {
    icon: "✅",
    title: "Tracks what matters",
    desc: "Todos, contacts, files, projects. Ask and it knows — no manual organization needed.",
  },
];
---

<section id="features" class="features">
  <span class="section-label">What it does</span>
  <h2 class="section-title">Everything an assistant should do.<br />Without the setup.</h2>
  <p class="section-sub">One conversation. Email, browser, memory, tasks — all connected.</p>
  <div class="feature-grid">
    {features.map((f) => (
      <article class="feature-card">
        <div class="feature-icon">{f.icon}</div>
        <h3 class="feature-title">{f.title}</h3>
        <p class="feature-desc">{f.desc}</p>
      </article>
    ))}
  </div>
</section>

<style>
  .features {
    padding: 64px 32px;
    max-width: 700px;
    margin: 0 auto;
    text-align: center;
  }
  .section-label {
    font-size: 11px;
    color: var(--ea-accent);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 8px;
    display: block;
  }
  .section-title {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ea-text-primary);
    margin: 0 0 12px;
    line-height: 1.2;
  }
  .section-sub {
    font-size: 14px;
    color: var(--ea-text-secondary);
    letter-spacing: -0.011em;
    max-width: 480px;
    margin: 0 auto 36px;
    line-height: 1.5;
  }
  .feature-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    max-width: 620px;
    margin: 0 auto;
    text-align: left;
  }
  .feature-card {
    background: var(--ea-bg-surface);
    border: 1px solid var(--ea-border-subtle);
    border-radius: var(--ea-radius-md);
    padding: 20px;
  }
  .feature-icon {
    width: 36px;
    height: 36px;
    background: var(--ea-accent-muted);
    border-radius: var(--ea-radius-md);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    margin-bottom: 12px;
    line-height: 1;
  }
  .feature-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--ea-text-primary);
    letter-spacing: -0.011em;
    margin: 0 0 4px;
  }
  .feature-desc {
    font-size: 12px;
    color: var(--ea-text-tertiary);
    letter-spacing: -0.005em;
    line-height: 1.5;
    margin: 0;
  }
</style>
```

---

### Task 5: Build Differentiation Component (4 Pillars)

**Files:**
- Create: `src/components/Differentiation.astro`

- [ ] **Step 1: Create Differentiation.astro**

```astro
---

const pillars = [
  {
    icon: "🧠",
    title: "Knows you, not just your prompts",
    desc: "Learns your preferences, corrections, and workflows from every conversation. Gets better the more you use it.",
    contrast: "Other assistants start fresh every time.",
  },
  {
    icon: "⚡",
    title: "Does things, not just chat",
    desc: "Reads your email, browses websites, manages tasks, searches files. Real actions, not just answers.",
    contrast: "Other assistants can only reply.",
  },
  {
    icon: "🔒",
    title: "Your data stays yours",
    desc: "Runs locally. Per-user storage. No cloud training on your conversations.",
    contrast: "Other assistants send your data to their servers.",
  },
  {
    icon: "🔄",
    title: "Any model you want",
    desc: "Works with OpenAI, Anthropic, Google, Ollama — 4,000+ models. No vendor lock-in.",
    contrast: "Other assistants force you into one model.",
  },
];
---

<section class="diff">
  <span class="section-label">How it's different</span>
  <h2 class="section-title">Not just another chatbot.<br />An actual assistant.</h2>
  <p class="section-sub">Most AI tools reply to what you say. Yours remembers what you mean.</p>
  <div class="pillars">
    {pillars.map((p) => (
      <article class="pillar">
        <div class="pillar-icon">{p.icon}</div>
        <div class="pillar-body">
          <h3 class="pillar-title">{p.title}</h3>
          <p class="pillar-desc">{p.desc}</p>
          <p class="pillar-contrast">{p.contrast}</p>
        </div>
      </article>
    ))}
  </div>
</section>

<style>
  .diff {
    padding: 64px 32px;
    max-width: 680px;
    margin: 0 auto;
    text-align: center;
  }
  .section-label {
    font-size: 11px;
    color: var(--ea-accent);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 8px;
    display: block;
  }
  .section-title {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ea-text-primary);
    margin: 0 0 12px;
    line-height: 1.2;
  }
  .section-sub {
    font-size: 14px;
    color: var(--ea-text-secondary);
    letter-spacing: -0.011em;
    max-width: 480px;
    margin: 0 auto 36px;
    line-height: 1.5;
  }
  .pillars {
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 560px;
    margin: 0 auto;
    text-align: left;
  }
  .pillar {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    padding: 16px;
    background: var(--ea-bg-surface);
    border: 1px solid var(--ea-border-subtle);
    border-radius: var(--ea-radius-md);
  }
  .pillar-icon {
    flex-shrink: 0;
    width: 40px;
    height: 40px;
    background: var(--ea-accent-muted);
    border-radius: var(--ea-radius-md);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
  }
  .pillar-body { flex: 1; }
  .pillar-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--ea-text-primary);
    letter-spacing: -0.011em;
    margin: 0 0 2px;
  }
  .pillar-desc {
    font-size: 12px;
    color: var(--ea-text-tertiary);
    letter-spacing: -0.005em;
    line-height: 1.5;
    margin: 0 0 4px;
  }
  .pillar-contrast {
    font-size: 11px;
    color: var(--ea-text-tertiary);
    margin: 4px 0 0;
    padding-top: 4px;
    border-top: 1px solid var(--ea-border-subtle);
    opacity: 0.7;
  }
</style>
```

---

### Task 6: Build Trust Component

**Files:**
- Create: `src/components/Trust.astro`

- [ ] **Step 1: Create Trust.astro**

```astro
---

const items = [
  { icon: "📖", title: "Open source", desc: "MIT license. Every line of code on GitHub. Audit it, fork it, contribute." },
  { icon: "🔒", title: "Runs locally", desc: "Your data stays on your machine. No cloud. No training on your conversations." },
  { icon: "🔄", title: "Your model, your choice", desc: "OpenAI, Anthropic, Google, Ollama — 4,000+ models. No lock-in." },
];
---

<section class="trust">
  <span class="section-label">Why trust EA</span>
  <h2 class="section-title">Built for you. Open for everyone.</h2>
  <div class="trust-grid">
    {items.map((item) => (
      <article class="trust-card">
        <div class="trust-icon">{item.icon}</div>
        <h3 class="trust-title">{item.title}</h3>
        <p class="trust-desc">{item.desc}</p>
      </article>
    ))}
  </div>
</section>

<style>
  .trust {
    padding: 48px 32px 24px;
    max-width: 700px;
    margin: 0 auto;
    text-align: center;
  }
  .section-label {
    font-size: 11px;
    color: var(--ea-accent);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 8px;
    display: block;
  }
  .section-title {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ea-text-primary);
    margin: 0 0 28px;
    line-height: 1.2;
  }
  .trust-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 12px;
  }
  .trust-card {
    background: var(--ea-bg-surface);
    border: 1px solid var(--ea-border-subtle);
    border-radius: var(--ea-radius-md);
    padding: 20px 16px;
  }
  .trust-icon {
    font-size: 24px;
    margin-bottom: 10px;
  }
  .trust-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--ea-text-primary);
    letter-spacing: -0.011em;
    margin: 0 0 4px;
  }
  .trust-desc {
    font-size: 12px;
    color: var(--ea-text-tertiary);
    letter-spacing: -0.005em;
    line-height: 1.5;
    margin: 0;
  }
</style>
```

---

### Task 7: Build CTA Component

**Files:**
- Create: `src/components/CTA.astro`

- [ ] **Step 1: Create CTA.astro**

```astro
---
import LogoConnected from "./LogoConnected.astro";
---

<section id="cta" class="cta">
  <h2 class="cta-title">Start with an assistant<br />that gets you.</h2>
  <p class="cta-sub">Free and open source. Runs on your machine. Connects to the model you choose.</p>
  <div class="cta-buttons">
    <a href="#" class="btn-primary">Download for macOS</a>
    <a href="https://github.com" class="btn-secondary">View on GitHub</a>
  </div>
</section>

<style>
  .cta {
    text-align: center;
    padding: 56px 32px;
    max-width: 600px;
    margin: 0 auto;
  }
  .cta-title {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ea-text-primary);
    margin: 0 0 10px;
    line-height: 1.2;
  }
  .cta-sub {
    font-size: 14px;
    color: var(--ea-text-secondary);
    letter-spacing: -0.011em;
    max-width: 440px;
    margin: 0 auto 28px;
    line-height: 1.5;
  }
  .cta-buttons {
    display: flex;
    justify-content: center;
    gap: 10px;
  }
  .btn-primary {
    background: var(--ea-accent);
    color: var(--ea-text-inverse);
    padding: 12px 28px;
    border-radius: var(--ea-radius-sm);
    font-weight: 500;
    font-size: 14px;
    text-decoration: none;
    display: inline-block;
  }
  .btn-secondary {
    background: transparent;
    color: var(--ea-text-primary);
    padding: 12px 28px;
    border-radius: var(--ea-radius-sm);
    border: 1px solid var(--ea-border-default);
    font-size: 14px;
    text-decoration: none;
    display: inline-block;
  }
</style>
```

---

### Task 8: Build Footer Component

**Files:**
- Create: `src/components/Footer.astro`

- [ ] **Step 1: Create Footer.astro**

```astro
---
import LogoConnected from "./LogoConnected.astro";
---

<footer class="footer">
  <div class="footer-inner">
    <div class="footer-brand">
      <div class="footer-logo">
        <LogoConnected size={20} />
        <span class="footer-wordmark">Assistant</span>
      </div>
      <p class="footer-tagline">Your AI assistant. One that gets you.</p>
    </div>
    <div class="footer-col">
      <h4 class="footer-head">Product</h4>
      <a href="#features" class="footer-link">Features</a>
      <a href="/docs" class="footer-link">Docs</a>
      <a href="https://github.com" class="footer-link">GitHub</a>
    </div>
    <div class="footer-col">
      <h4 class="footer-head">Community</h4>
      <a href="#" class="footer-link">Discord</a>
      <a href="#" class="footer-link">Twitter</a>
      <a href="#" class="footer-link">Contributing</a>
    </div>
    <div class="footer-col">
      <h4 class="footer-head">Legal</h4>
      <a href="#" class="footer-link">License (MIT)</a>
      <a href="#" class="footer-link">Privacy</a>
    </div>
  </div>
  <div class="footer-bottom">
    <span>&copy; 2026 Assistant</span>
    <div class="footer-socials">
      <a href="#" class="footer-social-link">GitHub</a>
      <a href="#" class="footer-social-link">Discord</a>
      <a href="#" class="footer-social-link">Twitter</a>
    </div>
  </div>
</footer>

<style>
  .footer {
    background: var(--ea-bg-surface);
    border-top: 1px solid var(--ea-border-subtle);
    padding: 32px 28px 24px;
  }
  .footer-inner {
    display: grid;
    grid-template-columns: 2fr 1fr 1fr 1fr;
    gap: 24px;
    max-width: 900px;
    margin: 0 auto 24px;
  }
  .footer-logo {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  .footer-wordmark {
    font-size: 13px;
    font-weight: 600;
    color: var(--ea-text-primary);
    letter-spacing: -0.011em;
  }
  .footer-tagline {
    font-size: 12px;
    color: var(--ea-text-tertiary);
    line-height: 1.5;
    margin: 0;
  }
  .footer-col {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .footer-head {
    font-size: 11px;
    color: var(--ea-text-secondary);
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin: 0;
  }
  .footer-link {
    font-size: 12px;
    color: var(--ea-text-tertiary);
    text-decoration: none;
  }
  .footer-link:hover {
    color: var(--ea-text-secondary);
  }
  .footer-bottom {
    border-top: 1px solid var(--ea-border-subtle);
    padding-top: 16px;
    display: flex;
    justify-content: space-between;
    max-width: 900px;
    margin: 0 auto;
    font-size: 11px;
    color: var(--ea-text-tertiary);
  }
  .footer-socials {
    display: flex;
    gap: 12px;
  }
  .footer-social-link {
    color: var(--ea-text-tertiary);
    text-decoration: none;
  }
  .footer-social-link:hover {
    color: var(--ea-text-secondary);
  }
</style>
```

---

### Task 9: Wire Up Landing Page

**Files:**
- Modify: `src/pages/index.astro`

- [ ] **Step 1: Replace index.astro content**

```astro
---
import LandingLayout from "../layouts/LandingLayout.astro";
import Nav from "../components/Nav.astro";
import Hero from "../components/Hero.astro";
import Features from "../components/Features.astro";
import Differentiation from "../components/Differentiation.astro";
import Trust from "../components/Trust.astro";
import CTA from "../components/CTA.astro";
import Footer from "../components/Footer.astro";
---

<LandingLayout>
  <Nav />
  <main>
    <Hero />
    <Features />
    <Differentiation />
    <Trust />
    <CTA />
  </main>
  <Footer />
</LandingLayout>
```

---

### Task 10: Create Docs Page Skeleton

**Files:**
- Create: `src/pages/docs.astro`

- [ ] **Step 1: Create docs.astro**

```astro
---
import LandingLayout from "../layouts/LandingLayout.astro";
import Nav from "../components/Nav.astro";
import Footer from "../components/Footer.astro";
---

<LandingLayout>
  <Nav />
  <main style="max-width:700px;margin:0 auto;padding:48px 32px;">
    <h1 style="font-size:28px;font-weight:600;letter-spacing:-0.02em;margin:0 0 8px;">Documentation</h1>
    <p style="font-size:14px;color:var(--ea-text-secondary);letter-spacing:-0.011em;margin:0 0 32px;line-height:1.5;">
      Everything you need to get started with Assistant.
    </p>

    <section style="margin-bottom:32px;">
      <h2 style="font-size:17px;font-weight:600;letter-spacing:-0.012em;margin:0 0 12px;color:var(--ea-text-primary);">Quickstart</h2>
      <p style="font-size:13px;color:var(--ea-text-tertiary);line-height:1.6;margin:0;">
        Documentation coming soon. Visit our GitHub repository for setup instructions, API reference, and deployment guides.
      </p>
    </section>

    <section style="margin-bottom:32px;">
      <h2 style="font-size:17px;font-weight:600;letter-spacing:-0.012em;margin:0 0 12px;color:var(--ea-text-primary);">Deployment</h2>
      <p style="font-size:13px;color:var(--ea-text-tertiary);line-height:1.6;margin:0;">
        Assistant runs on macOS, Linux, and Windows. Deploy solo on your machine or as a shared service.
      </p>
    </section>
  </main>
  <Footer />
</LandingLayout>
```

---

### Task 11: Build Verification

- [ ] **Step 1: Build the site**

Run: `npm run build`
Expected: `1 page(s) built` (for now; will show 2 after docs page is created)

- [ ] **Step 2: Verify dev server starts**

Run: `npm run dev`
Expected: Dev server starts on `localhost:4321`. Open in browser and verify:
- Hero section renders with "One that gets you" headline
- Features 2×2 grid shows 4 cards
- Differentiation 4 pillars render
- Trust 3 cards render
- CTA renders with buttons
- Footer renders with 4 columns
- Navigation links work
- Docs page loads at /docs
- Design tokens match spec (dark theme, emerald accent, Inter font)

- [ ] **Step 3: Commit**

```bash
git add site/
git add docs/superpowers/plans/2026-05-25-intro-website-implementation.md
git commit -m "feat: scaffold intro website with landing page components"
```
