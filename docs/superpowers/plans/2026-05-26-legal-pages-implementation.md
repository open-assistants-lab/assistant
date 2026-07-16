# Legal Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Privacy Policy and Terms of Service pages to the intro website, with footer navigation links.

**Architecture:** Two new Astro page files at `/privacy` and `/terms`, plus a footer link update. Both pages reuse the existing `LandingLayout` + `Nav` + `Footer` template matching the `/docs` page pattern.

**Tech Stack:** Astro, static site generation

**Existing files to know:**
- `site/src/layouts/LandingLayout.astro` — base layout with nav and footer slots
- `site/src/components/Nav.astro` — site navigation
- `site/src/components/Footer.astro` — site footer (needs link updates)
- `site/src/pages/docs.astro` — reference pattern for legal pages

---

### Task 1: Create Privacy Policy page

**Files:**
- Create: `site/src/pages/privacy.astro`

- [ ] **Step 1: Write `site/src/pages/privacy.astro`**

```astro
---
import LandingLayout from "../layouts/LandingLayout.astro";
import Nav from "../components/Nav.astro";
import Footer from "../components/Footer.astro";
---

<LandingLayout>
  <Nav />
  <main class="legal">
    <h1>Privacy Policy</h1>
    <p class="legal-meta"><strong>Open Assistants Lab</strong> &middot; Last updated: 26 May 2026</p>

    <section>
      <h2>Information We Collect</h2>
      <p><strong>Website (Google Analytics).</strong> When you visit openassistants.org, we use Google Analytics to collect page views, referrer information, browser and device details, and anonymized IP addresses. This helps us understand how the site is used and improve it.</p>
      <p><strong>Assistant software.</strong> If you choose to share troubleshooting data, we may collect diagnostic information such as logs and crash reports. You control whether this data is sent. Assistant does not collect or transmit your personal content, conversations, files, or data by default.</p>
    </section>

    <section>
      <h2>How We Use Information</h2>
      <p>We use the information we collect to:</p>
      <ul>
        <li>Improve the website and software</li>
        <li>Understand aggregated usage patterns</li>
        <li>Fix bugs and troubleshoot issues</li>
      </ul>
    </section>

    <section>
      <h2>Information We Do Not Collect or Share</h2>
      <p>Assistant does not transmit your data to any third party beyond the LLM provider you explicitly configure and connect to. You choose your own provider and manage your own API keys.</p>
      <p>We do not sell, rent, or trade personal information. Your conversations, files, email, and contacts remain on your machine.</p>
    </section>

    <section>
      <h2>Cookies</h2>
      <p>Google Analytics uses cookies to distinguish users. You can opt out of Google Analytics by installing the <a href="https://tools.google.com/dlpage/gaoptout">Google Analytics Opt-Out Browser Add-on</a>.</p>
    </section>

    <section>
      <h2>Your Rights</h2>
      <p>Under the Australian Privacy Act, you have the right to:</p>
      <ul>
        <li>Access personal information we hold about you</li>
        <li>Request correction of inaccurate information</li>
        <li>Request deletion of your information</li>
      </ul>
      <p>To exercise these rights, contact us at <a href="mailto:eddy@openassistants.org">eddy@openassistants.org</a>.</p>
    </section>

    <section>
      <h2>Complaints</h2>
      <p>If you are unsatisfied with our response to a privacy concern, you may lodge a complaint with the <a href="https://www.oaic.gov.au">Office of the Australian Information Commissioner (OAIC)</a>.</p>
    </section>

    <section>
      <h2>Changes to This Policy</h2>
      <p>We may update this policy from time to time. Changes will be posted on this page.</p>
    </section>

    <section>
      <h2>Contact</h2>
      <p><strong>Open Assistants Lab</strong><br />
      <a href="mailto:eddy@openassistants.org">eddy@openassistants.org</a></p>
      <p>This policy is governed by the laws of New South Wales, Australia.</p>
    </section>
  </main>
  <Footer />
</LandingLayout>

<style>
  .legal {
    max-width: 680px;
    margin: 0 auto;
    padding: 48px 32px 64px;
  }
  .legal h1 {
    font-size: 32px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ea-text-primary);
    margin: 0 0 4px;
  }
  .legal-meta {
    font-size: 13px;
    color: var(--ea-text-tertiary);
    margin: 0 0 40px;
  }
  .legal section {
    margin-bottom: 32px;
  }
  .legal section:last-child {
    margin-bottom: 0;
  }
  .legal h2 {
    font-size: 18px;
    font-weight: 600;
    color: var(--ea-text-primary);
    margin: 0 0 12px;
    letter-spacing: -0.011em;
  }
  .legal p, .legal li {
    font-size: 14px;
    line-height: 1.7;
    color: var(--ea-text-secondary);
    margin: 0 0 8px;
  }
  .legal ul {
    margin: 0 0 8px;
    padding-left: 20px;
  }
  .legal li {
    margin-bottom: 4px;
  }
  .legal a {
    color: var(--ea-accent);
    text-decoration: none;
  }
  .legal a:hover {
    text-decoration: underline;
  }
</style>
```

- [ ] **Step 2: Verify build**

Run: `npm run build` in `site/`
Expected: Build passes, generates `/privacy/index.html`

---

### Task 2: Create Terms of Service page

**Files:**
- Create: `site/src/pages/terms.astro`

- [ ] **Step 1: Write `site/src/pages/terms.astro`**

```astro
---
import LandingLayout from "../layouts/LandingLayout.astro";
import Nav from "../components/Nav.astro";
import Footer from "../components/Footer.astro";
---

<LandingLayout>
  <Nav />
  <main class="legal">
    <h1>Terms of Service</h1>
    <p class="legal-meta"><strong>Open Assistants Lab</strong> &middot; Last updated: 26 May 2026</p>

    <section>
      <h2>Acceptance of Terms</h2>
      <p>By accessing the website at openassistants.org or using the Assistant software, you agree to be bound by these terms. If you do not agree, do not use the software or website.</p>
    </section>

    <section>
      <h2>Description of Service</h2>
      <p>Assistant is open source software released under the MIT License. The software connects to third-party large language model (LLM) providers at your direction. The website provides information about the software and the project.</p>
    </section>

    <section>
      <h2>User Responsibilities</h2>
      <p>You agree to:</p>
      <ul>
        <li>Comply with all applicable laws when using the software and website</li>
        <li>Not misuse the software or website for illegal or unauthorized purposes</li>
        <li>Choose your own LLM provider and manage your own API keys</li>
        <li>Comply with your chosen LLM provider's terms of service</li>
        <li>Take responsibility for the content you submit through the software</li>
      </ul>
    </section>

    <section>
      <h2>Intellectual Property</h2>
      <p>The Assistant software is licensed under the <a href="https://opensource.org/licenses/MIT">MIT License</a>. You are free to use, copy, modify, merge, publish, distribute, sublicense, and sell copies of the software, subject to the terms of that license.</p>
      <p>The Open Assistants Lab name, logo, and branding may not be used without prior written permission.</p>
    </section>

    <section>
      <h2>Disclaimer of Warranties</h2>
      <p>The Assistant software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and noninfringement.</p>
      <p>The website is provided on an "as is" and "as available" basis.</p>
    </section>

    <section>
      <h2>Limitation of Liability</h2>
      <p>In no event shall Open Assistants Lab be liable for any claim, damages, or other liability arising from the use of the software, the website, or outputs from LLM providers connected through the software, whether in contract, tort, or otherwise.</p>
    </section>

    <section>
      <h2>Governing Law</h2>
      <p>These terms are governed by the laws of New South Wales, Australia. Any disputes shall be resolved in the courts of New South Wales.</p>
    </section>

    <section>
      <h2>Contact</h2>
      <p><strong>Open Assistants Lab</strong><br />
      <a href="mailto:eddy@openassistants.org">eddy@openassistants.org</a></p>
    </section>
  </main>
  <Footer />
</LandingLayout>

<style>
  .legal {
    max-width: 680px;
    margin: 0 auto;
    padding: 48px 32px 64px;
  }
  .legal h1 {
    font-size: 32px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ea-text-primary);
    margin: 0 0 4px;
  }
  .legal-meta {
    font-size: 13px;
    color: var(--ea-text-tertiary);
    margin: 0 0 40px;
  }
  .legal section {
    margin-bottom: 32px;
  }
  .legal section:last-child {
    margin-bottom: 0;
  }
  .legal h2 {
    font-size: 18px;
    font-weight: 600;
    color: var(--ea-text-primary);
    margin: 0 0 12px;
    letter-spacing: -0.011em;
  }
  .legal p, .legal li {
    font-size: 14px;
    line-height: 1.7;
    color: var(--ea-text-secondary);
    margin: 0 0 8px;
  }
  .legal ul {
    margin: 0 0 8px;
    padding-left: 20px;
  }
  .legal li {
    margin-bottom: 4px;
  }
  .legal a {
    color: var(--ea-accent);
    text-decoration: none;
  }
  .legal a:hover {
    text-decoration: underline;
  }
</style>
```

- [ ] **Step 2: Verify build**

Run: `npm run build` in `site/`
Expected: Build passes, generates `/terms/index.html`

---

### Task 3: Update Footer navigation links

**Files:**
- Modify: `site/src/components/Footer.astro`

- [ ] **Step 1: Wire up existing Privacy link and add Terms of Service link**

In `Footer.astro`, change the Legal column from:
```astro
    <div class="footer-col">
      <h4 class="footer-head">Legal</h4>
      <a href="#" class="footer-link">License (MIT)</a>
      <a href="#" class="footer-link">Privacy</a>
    </div>
```
to:
```astro
    <div class="footer-col">
      <h4 class="footer-head">Legal</h4>
      <a href="#" class="footer-link">License (MIT)</a>
      <a href="/privacy" class="footer-link">Privacy</a>
      <a href="/terms" class="footer-link">Terms of Service</a>
    </div>
```

- [ ] **Step 2: Verify build**

Run: `npm run build` in `site/`
Expected: Build passes, 4 pages generated (index, docs, privacy, terms)

---

### Task 4: Final build verification

**Files:** None (verification only)

- [ ] **Step 1: Run full build**

Run: `npm run build` in `site/`
Expected: Build passes, 4 pages built

- [ ] **Step 2: Preview dev server**

Run: `npm run dev` in `site/`
Expected: Dev server starts. Visit `/`, `/privacy`, `/terms` to verify all pages render correctly with proper layout, nav, and footer.
