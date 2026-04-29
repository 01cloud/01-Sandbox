# 01 Sandbox — Marketing Site

A modern marketing website for **01 Sandbox**, a platform for executing untrusted code in hardened, isolated environments.

Built with **React + Vite + TypeScript + Tailwind CSS** and **shadcn/ui**.

---

## Prerequisites

Make sure the following are installed on your machine:

- **Node.js** `>= 18` (LTS recommended) — [download](https://nodejs.org/)
- **npm** `>= 9` (ships with Node), or **bun** / **pnpm** / **yarn** if you prefer
- **Git** — [download](https://git-scm.com/)

Verify installations:

```bash
node -v
npm -v
git --version
```

---

## Getting Started

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd <your-repo-folder>
```

### 2. Install dependencies

Using **npm**:

```bash
npm install
```

Or with **bun**:

```bash
bun install
```

### 3. Start the development server

```bash
npm run dev
```

The app will be available at **http://localhost:8080** (or the next free port shown in the terminal).

The dev server supports **hot module replacement** — edits to source files are reflected instantly in the browser.

---

## Available Scripts

| Command | Description |
| --- | --- |
| `npm run dev` | Start the local development server |
| `npm run build` | Build the production bundle into `dist/` |
| `npm run build:dev` | Build with development mode settings |
| `npm run preview` | Preview the production build locally |
| `npm run lint` | Run ESLint across the codebase |
| `npm test` | Run the Vitest test suite |

---

## Project Structure

```
.
├── public/                  # Static assets served as-is
├── src/
│   ├── assets/              # Images & media imported by components
│   ├── components/          # Page sections + shadcn/ui primitives
│   │   ├── ui/              # shadcn/ui components
│   │   ├── HeroSection.tsx
│   │   ├── PillarsSection.tsx
│   │   ├── FeaturesSection.tsx
│   │   ├── SecurityPipeline.tsx
│   │   ├── ArchitectureSection.tsx
│   │   ├── WhySection.tsx
│   │   ├── Navbar.tsx
│   │   ├── ThemeToggle.tsx
│   │   └── Footer.tsx
│   ├── hooks/               # Reusable React hooks
│   ├── lib/                 # Utilities (cn helper, etc.)
│   ├── pages/               # Route-level pages (Index, NotFound)
│   ├── App.tsx              # Root app + providers (Theme, Router, Query)
│   ├── main.tsx             # React entrypoint
│   └── index.css            # Design tokens (light/dark) + Tailwind layers
├── tailwind.config.ts       # Tailwind theme & token mapping
├── vite.config.ts           # Vite configuration
└── package.json
```

---

## Theming (Light / Dark Mode)

Theme switching is powered by [`next-themes`](https://github.com/pacocoursey/next-themes).

- All colors are defined as **HSL CSS variables** in `src/index.css` under `:root` (light) and `.dark` (dark).
- Components consume **semantic tokens** (`bg-background`, `text-foreground`, `border-border`, etc.) — never hard-coded colors.
- The toggle lives in the navbar (`src/components/ThemeToggle.tsx`).

To adjust colors or add a new accent, edit the variables in `src/index.css` and the corresponding entries in `tailwind.config.ts`.

---

## Tech Stack

- **React 18** + **TypeScript 5**
- **Vite 5** — build tool & dev server
- **Tailwind CSS v3** + **tailwindcss-animate**
- **shadcn/ui** — accessible component primitives (Radix UI)
- **framer-motion** — section animations
- **lucide-react** — icon set
- **next-themes** — light/dark mode
- **react-router-dom** — routing
- **@tanstack/react-query** — data layer (ready for API integration)
- **sonner** — premium toast notifications
- **react-google-recaptcha** — bot & spam protection

---

## Building for Production

```bash
npm run build
```

The optimized output is written to `dist/`. Preview it locally with:

```bash
npm run preview
```

You can deploy the contents of `dist/` to any static host (Vercel, Netlify, Cloudflare Pages, S3 + CloudFront, GitHub Pages, etc.).

---

## Lead Generation & Security

The website is integrated with **Google Chat Webhooks** for lead delivery and **Google reCAPTCHA** for spam protection.

### 1. Booking a Demo (Google Chat)
The "Book a Demo" form sends requests directly to a Google Chat Space webhook. To configure your destination:
1. Get a webhook URL from your Google Chat space.
2. In local development, add it to `.env`:
   ```env
   VITE_GOOGLE_CHAT_WEBHOOK_URL=your_webhook_url
   ```

### 2. Spam Protection (reCAPTCHA)
The form is protected by an invisible Honeypot field and a reCAPTCHA checkbox. To move from testing to production:
1. Go to [Google reCAPTCHA Admin Console](https://www.google.com/recaptcha/admin).
2. Register your domain and configure your key.
3. **Local Development**: Copy your **Site Key** and paste it into the `.env` file:
   ```env
   VITE_RECAPTCHA_SITE_KEY=your_production_site_key
   ```

### 3. Docker / ECS Production Build
Because Vite statically embeds environment variables at build-time, you must pass BOTH the reCAPTCHA site key and the Google Chat Webhook URL as `--build-arg` flags when building the Docker image for your ECS deployment:

```bash
docker build \
  --build-arg VITE_RECAPTCHA_SITE_KEY=your_production_site_key \
  --build-arg VITE_GOOGLE_CHAT_WEBHOOK_URL="your_webhook_url" \
  -t your-ecr-repo-name:latest .
```

---

## Troubleshooting

- **Port already in use** — stop the other process or run `PORT=3000 npm run dev`.
- **Stale dependencies** — delete `node_modules` and the lockfile, then reinstall:
  ```bash
  rm -rf node_modules package-lock.json
  npm install
  ```
- **TypeScript errors after pulling changes** — restart your editor's TS server or run `npx tsc --noEmit` to see all errors.

---

## License

Proprietary — © 01Security Inc. All rights reserved.


site key: 6LeKiswsAAAAAMF_8fxlr1z7VCRQ0zr-oFfncfd0

secret key: 6LeKiswsAAAAAJNdSGKFnKqh9SxsfrsfyHPc7SN0
