# Assistant — Legal Pages Design

**Date:** 2026-05-26
**Status:** Approved design
**Framework:** Astro
**Entity:** Open Assistants Lab (openassistants.org)
**Jurisdiction:** New South Wales, Australia
**Contact:** eddy@openassistants.org

---

## 1. Pages

Two dedicated pages following the existing site layout:

| Page | Route | Purpose |
|------|-------|---------|
| Privacy Policy | `/privacy` | Australian Privacy Act compliance, data practices disclosure |
| Terms of Service | `/terms` | OSS software terms, liability disclaimer, governing law |

Both pages use `LandingLayout` + `Nav` + `Footer` (same template as `/docs`).

---

## 2. Privacy Policy — Content Structure

### 2.1 Header
- Title: "Privacy Policy"
- Entity: Open Assistants Lab
- Last updated: 26 May 2026

### 2.2 Information We Collect
- **Website (Google Analytics)**: Page views, referrer, browser/device info, IP address (anonymized)
- **Assistant software**: Optional troubleshooting data if the user chooses to share it. No personal content or user data is collected by default.

### 2.3 How We Use Information
- Improve the website and software
- Understand usage patterns (aggregated)
- Fix bugs and troubleshoot issues

### 2.4 Information We Do NOT Collect or Share
- Assistant does not transmit user data to any third party beyond the LLM provider the user explicitly configures
- We do not sell, rent, or trade personal information
- User conversations, files, email, and contacts remain on the user's machine

### 2.5 Cookies
- Google Analytics uses cookies to distinguish users
- Users can opt out via the [Google Analytics Opt-Out Browser Add-on](https://tools.google.com/dlpage/gaoptout)

### 2.6 Your Rights (Australian Privacy Act)
- Access personal information we hold
- Request correction of inaccurate information
- Request deletion of information
- Contact: eddy@openassistants.org

### 2.7 Complaints
- If unsatisfied with our response, you may lodge a complaint with the Office of the Australian Information Commissioner (OAIC)

### 2.8 Changes
- Policy may be updated. Changes posted to this page.

### 2.9 Governing Law
- New South Wales, Australia

---

## 3. Terms of Service — Content Structure

### 3.1 Header
- Title: "Terms of Service"
- Entity: Open Assistants Lab
- Last updated: 26 May 2026

### 3.2 Acceptance
- By accessing the website or using Assistant software, you agree to these terms
- If you do not agree, do not use the software or website

### 3.3 Description of Service
- Assistant is open source software released under the MIT license
- The software connects to third-party LLM providers at the user's direction
- The website provides information about the software and project

### 3.4 User Responsibilities
- Comply with all applicable laws
- Do not misuse the software or website for illegal purposes
- You are responsible for your choice of LLM provider, API keys, and compliance with that provider's terms
- You are responsible for the content you submit through the software

### 3.5 Intellectual Property
- The Assistant software is licensed under the MIT License
- You are free to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
- The Open Assistants Lab name and branding may not be used without permission

### 3.6 Disclaimer of Warranties
- The software is provided "as is", without warranty of any kind (standard MIT disclaimer)
- The website is provided on an "as is" basis

### 3.7 Limitation of Liability
- Open Assistants Lab is not liable for damages arising from use of the software, website, or LLM provider outputs
- This includes direct, indirect, incidental, consequential, and special damages

### 3.8 Governing Law
- These terms are governed by the laws of New South Wales, Australia
- Any disputes shall be resolved in the courts of New South Wales

### 3.9 Contact
- eddy@openassistants.org

---

## 4. Navigation Updates

- **Footer "Privacy" link**: Update `href="#"` → `/privacy` in `Footer.astro`
- **Footer add "Terms"**: Add a new link in the Legal column: `<a href="/terms" class="footer-link">Terms of Service</a>`

---

## 5. Layout & Styling

- Dark theme matching existing site (same background, text colors, typography)
- Content width: max 680px, centered (matching docs page)
- Typography: section headings, body text using Inter font
- Legal text is plain prose — no cards, no grids, no icons
- Responsive: standard padding scales down on mobile

---

## 6. Dependencies

- No new dependencies
- Astro static generation handles the pages at build time
