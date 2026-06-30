# Public Sanitization Guide

This guide documents the process used to turn an internal Alexa bridge repo into this public-safe release. It is intentionally generic: use your own private string patterns and placeholders.

## Goal

Publish the reusable product code without exposing:

- private names, handles, emails, phone numbers, chat IDs, or account IDs
- real hostnames, tunnel hostnames, LAN names, or service URLs
- local home paths or machine-specific paths
- tokens, OAuth material, cookies, private keys, or generated credentials
- private status notes, smoke-test history, incident details, or billing/provider assumptions
- generated artifacts such as zip files, caches, logs, bytecode, and coverage output
- old git history that may contain any of the above

## Release Shape

Use two repositories:

- Private source repo: keeps deployment overlays, local notes, live scripts, and private history.
- Public repo: fresh sanitized tree with generic product code, docs, examples, and tests.

For a first public release, prefer a fresh one-commit public history. Do not rewrite a private repo in place and then make it public unless you have separately proven every old object and ref is safe.

## Step 1. Inventory The Private Repo

From the private repo, list files without dumping generated artifacts:

```bash
find . -maxdepth 3 -type f -not -path './.git/*' | sort
```

Classify each file:

- Keep: generic source, tests, public examples, reusable docs.
- Generalize: docs or config templates with real values replaced by placeholders.
- Drop: private status files, deployment notes with live details, generated artifacts, bytecode, logs, tunnel wrappers, private fixtures, and local worker scripts.

## Step 2. Build A Fresh Public Tree

Create a clean public directory and copy only allowlisted files:

```bash
mkdir -p /path/to/public-repo
cp -R lambda scripts tests config skill-package /path/to/public-repo/
cp README.md ask-resources.json .gitignore /path/to/public-repo/
```

Adjust the allowlist for your project. Starting from an allowlist is safer than copying everything and deleting risky files.

## Step 3. Sanitize Product Strings

Replace private strings with boring placeholders:

- private agent names -> `example-agent`, `the assistant`, or `OpenClaw`
- real destinations -> `example-destination`
- real skill IDs -> `amzn1.ask.skill.example`
- real URLs -> `https://example.com/hooks/agent`
- real paths -> `openclaw`, `/path/to/...`, or environment variables
- real timezones -> configurable `OPENCLAW_ALEXA_TIME_ZONE`

Also update tests so they assert the public placeholders, not the private values.

## Step 4. Replace Private Docs With Public Docs

Write new public docs from scratch when private docs are full of deployment history.

This release kept the reusable deployment concepts but removed:

- live tunnel hostnames
- exact private launch/test dates
- private smoke-test notes
- local worker/offload instructions
- specific account IDs and destinations
- scripts that encoded one deployment topology

## Step 5. Run Source Checks

Run tests and syntax checks from the public tree:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile lambda/alexa_bridge.py lambda/lambda_function.py scripts/inspect-hooks-config.py scripts/temp-hook-proxy.py
bash -n scripts/package-lambda.sh scripts/deployment-preflight.sh
python3 -m json.tool ask-resources.json
python3 -m json.tool skill-package/skill.json
python3 -m json.tool skill-package/interactionModels/custom/en-US.json
```

## Step 6. Run Secret And Identity Scans

Run a secret scanner on the uncommitted tree:

```bash
gitleaks detect --source . --no-banner --redact --verbose --no-git
```

Run a project-specific identity scan. Replace the placeholder regex parts with your private values before running:

```bash
rg -n --hidden -g '!.git/**' -g '!build/**' -g '!__pycache__/**' -i \
  '<private-name>|<private-handle>|<private-email>|<private-host>|<private-url>|<private-path>|<private-chat-id>|<secret-key-pattern>' .
```

Review every hit. Some matches may be expected placeholders; real private hits must be removed before publishing.

## Step 7. Initialize Fresh History

Only after the tree is sanitized:

```bash
git init -b main
git add .
git commit -m "Initial sanitized public release"
```

Then scan committed history:

```bash
gitleaks detect --source . --no-banner --redact --verbose
```

## Step 8. Publish

If a private GitHub repo already uses the desired public name, preserve it under a private `-private` name first. Then create the public repo from the sanitized tree.

Before treating the release as done:

- verify the public repo is public
- verify the private repo is still private
- verify branch protection is enabled for `main`
- verify scans still pass after the first public commit
- keep a private publication proof report outside the public repo

## Step 9. Future Updates

Keep the public repo as the canonical generic product. Future changes should flow like this:

1. Build generic improvements in public or port them into public deliberately.
2. Keep local deployment overlays private.
3. Run tests, secret scans, identity scans, and history scans before every public release.
4. Pull public improvements back into private deployments as needed.
