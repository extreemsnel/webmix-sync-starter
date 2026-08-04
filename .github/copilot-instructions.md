# Webmix Copilot Instructions

These instructions apply to every task in this repository.

## General

### Caveman Mode

Unless explicitly requested otherwise:

- No reasoning.
- No explanations.
- No summaries.
- No unnecessary text.
- Output only the requested code, diff or commands.
- Stop after the requested task is complete.

### Workflow

- Keep responses concise.
- Modify existing code instead of introducing new abstractions whenever possible.
- Inspect existing implementations before creating new ones.
- Reuse existing helpers, utilities and architecture whenever possible.
- Only inspect and modify files relevant to the task.
- Do not refactor unrelated code.
- Fix root causes instead of symptoms.
- If assumptions are required, ask one concise question before making changes.
- For large implementations, first provide a short implementation plan and wait for approval.

### Environment

- Prefer SSH and WP-CLI over wp-admin when investigating or modifying WordPress installations, if SSH access is available.
- SSH usernames are usually formatted as `<cpanel-user>@<server-ip>`. The cPanel username is often the same as the account or folder name.
- Local file changes are usually synchronized automatically by a watcher. Do not suggest FTP uploads or deployment steps unless explicitly requested.

## Output

- Prefer unified diffs or modified functions over complete files.
- Never repeat unchanged code.
- Keep output as short as possible.
- Do not use Markdown unless requested.

## Code Quality

- Keep changes minimal and targeted.
- Preserve existing architecture, naming and coding style.
- Preserve backwards compatibility unless instructed otherwise.
- Prioritize readability, maintainability and performance.
- Avoid unnecessary complexity.
- Avoid premature abstraction.
- When multiple valid solutions exist, prefer the simplest solution that fits the existing architecture.
- Do not introduce new dependencies unless explicitly requested or clearly justified.

## PHP

- Target PHP 8.4 unless the environment requires another version.
- Always use `declare(strict_types=1);`.
- Use typed properties, parameters and return types.
- Prefer early returns.
- Avoid deep nesting.
- Never suppress errors.
- Throw exceptions only when appropriate.

## WordPress

- Follow WordPress Coding Standards where practical.
- Follow the existing project structure for custom themes, plugins and applications.
- Prefer WordPress APIs over custom implementations.
- Prefer existing project helpers over creating new ones.
- Prefer WP-CLI over wp-admin instructions when applicable.
- Escape all output.
- Sanitize all user input.
- Validate all external data.
- Use nonces where appropriate.
- Never modify WordPress core.
- Never modify vendor code.

## WooCommerce

- Preserve backwards compatibility where practical.
- Preserve existing hooks and filters where possible.
- Do not break template overrides.
- Avoid unnecessary database queries.
- Consider object caching before introducing expensive operations.

## Database

- Prefer existing WordPress APIs over direct SQL.
- Use direct SQL only when there is a measurable performance or functional benefit.
- Always use prepared SQL statements.

## Performance

- Avoid unnecessary queries.
- Avoid unnecessary API calls.
- Avoid unnecessary filesystem operations.
- Avoid loading unnecessary assets.
- Consider cache implications before changing logic.

## Security

- Never hardcode credentials, tokens or secrets.
- Never expose sensitive information.
- Validate permissions before privileged actions.
- Prevent XSS, CSRF, SQL injection and path traversal.
- Never invent credentials, configuration values or secrets.

## Logging

- Keep logging concise and actionable.
- Remove temporary debugging before completing the task unless explicitly requested.

## Git

- Never use Git unless explicitly requested.
- Never commit, push, merge, rebase or rewrite history.
- Never perform destructive Git operations.

## Testing

- Only run tests if explicitly requested.
- Only add or update tests if explicitly requested.
- Do not run linters unless requested.
- Do not format unrelated files.

## Never

- Never invent APIs, classes, functions or configuration values.
- Never invent file paths.
- Never assume framework behaviour without inspecting the project.
- Never remove existing functionality unless explicitly requested.
- Never change public interfaces unless necessary.
- Never introduce breaking changes unless explicitly requested.