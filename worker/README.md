# Searchbrain chat Worker

A GitHub Pages site is only files, so it cannot keep a secret. If the website called the NVIDIA API itself, the
API key would be visible to everybody who opens the page. This small Cloudflare Worker is the answer: the key
lives only inside the Worker, the website sends it a question plus the startups it found, and the Worker streams the
answer of the AI model back.

```
browser (docs/)  --question + 12 startups-->  Worker (holds NVIDIA_API_KEY)  -->  NVIDIA API
                 <--------- answer ----------                                <--
```

Without the Worker the website still works: the Ask field then only searches the saved startups.

## Set it up (about 10 minutes, free)

You need a free Cloudflare account. The code is in `src/index.js`, the settings in `wrangler.toml`.

### Option A: with the Wrangler command line (needs Node.js 20 or newer)

```
cd worker
npx wrangler login
npx wrangler secret put NVIDIA_API_KEY     # paste your NVIDIA key when asked, it is not written to any file
npx wrangler deploy
```

`deploy` prints the address of the Worker, like `https://searchbrain-chat.<your-name>.workers.dev`.

### Option B: from the Cloudflare dashboard (no Node.js)

1. Cloudflare dashboard, **Workers & Pages**, **Create application**, import your GitHub repository.
2. Set the root directory to `worker`.
3. After the first deploy open the Worker, **Settings**, **Variables and Secrets**, add a **secret** named
   `NVIDIA_API_KEY` with your key, and deploy again.

Cloudflare changes its menus from time to time. If a name differs, the Workers documentation has the current steps.

### Then tell the website where the Worker is

Put the address into `docs/config.js`:

```js
workerUrl: "https://searchbrain-chat.<your-name>.workers.dev",
```

Commit and push. The Ask field now writes AI answers.

If your website is not at `https://jonkaliner.github.io`, change `ALLOWED_ORIGINS` in `wrangler.toml`
(comma separated for several sites) and deploy again. A call from any other website is refused.

## Start a Scout from the website (no GitHub page needed)

The same Worker can start the "Update" workflow with your request, so the **New Scout** button on the website works by
itself. It needs two more secrets:

1. **A GitHub token that can only start workflows.** GitHub, your profile picture, *Settings*, *Developer settings*,
   *Personal access tokens*, *Fine-grained tokens*, *Generate new token*.
   - Repository access: *Only select repositories*, choose `git-init-searchbrain`.
   - Permissions, *Repository permissions*: **Actions: Read and write**. Nothing else.
   - Set an expiry date (for example one year) and copy the token once.
2. **An access code** that only you know (a few random words). The website asks for it, so strangers cannot start Scouts.

Add both as secrets of the Worker (never in a file):

```
npx wrangler secret put GITHUB_PAT       # the token from step 1
npx wrangler secret put SCOUT_CODE       # your access code
```

(In the dashboard: Worker, *Settings*, *Variables and Secrets*.) Then open the website, click **New Scout**, type
the request and the access code once; the browser remembers the code. If the repository has another name, set
`GITHUB_REPO` in `wrangler.toml`.

## What protects it

The Worker address is public, so anyone who finds it could send questions. That is limited on purpose:

| Protection | Where |
|---|---|
| Only the website's origin may call it | `ALLOWED_ORIGINS`, checked in the code |
| Short questions (500 characters), at most 12 startups, at most 6 earlier turns, 30 KB in total | `LIMITS` in `src/index.js` |
| A fixed instruction and a fixed model, the caller cannot choose either | `SYSTEM_PROMPT`, `NVIDIA_MODEL` |
| At most 400 words-pieces (tokens) of answer | `max_tokens` |
| 10 questions per minute per visitor | `[[ratelimits]]` in `wrangler.toml` (approximate, needs Wrangler 4.36+) |
| The key never leaves the Worker, error details from NVIDIA are not passed on | `handle()` |

It is still not a locked door: someone who copies the website's headers can call it. If you ever see questions that
did not come from you, rotate the NVIDIA key (NVIDIA website, then `npx wrangler secret put NVIDIA_API_KEY`).

## Tests

`tests/web/worker.test.html` runs 32 checks against a fake NVIDIA API (wrong origin, too big, rate limit, streaming,
instructions hidden in the data, NVIDIA errors). Start a web server in the project folder and open the page:

```
.venv313\Scripts\python.exe -m http.server 8000      then open  http://localhost:8000/tests/web/worker.test.html
```

It must say `ALL PASSED`.
