# Antigravity Trading Accuracy Pack

This pack is a starting point for improving consistency and research integrity in a Python NSE trading project. It cannot guarantee model accuracy or profitable trading.

## Files
- `AGENTS.md`: concise always-on workspace instructions.
- `BACKTEST_VALIDATION.md`: acceptance standard for historical simulations.
- `.agents/rules/python-trading.md`: Python-specific rule activated for Python files.
- `.agents/skills/audit-backtest/SKILL.md`: reusable audit workflow.
- `PROMPTS.md`: task prompts for verification, audit, fixes, and independent checks.

## Install
1. Back up or commit your project before changing instructions.
2. Copy `AGENTS.md` and `BACKTEST_VALIDATION.md` into the repository root.
3. Copy the `.agents` directory into the repository root, preserving its folder structure.
4. In Antigravity, open Customizations > Rules and confirm the workspace rule is visible. Antigravity's current documentation says root `AGENTS.md`/`GEMINI.md` are always-on; every file in `.agents/rules/` needs valid YAML frontmatter and a supported trigger. Rules are scanned flatly in `.agents/rules/`.
5. Start a fresh agent conversation and run Prompt 1 from `PROMPTS.md`.
6. Do not let the agent edit strategy code until instruction loading is verified and a read-only baseline audit is complete.

## Important cautions
- Do not blindly replace existing project instructions; merge project-specific requirements and keep secrets out of the files.
- Review the generated files against the repository architecture before using them.
- Rules and prompts cannot force a model to be correct. Enforce critical behavior with executable tests, assertions, CI, code review, and reproducible run artifacts.
- For the final holdout, record the period and do not tune against it. If it is used for tuning, relabel it as development data.
- Treat Reddit posts and repository READMEs as leads, not verified evidence.

## Recommended staged rollout
1. Verify loading only.
2. Read-only audit.
3. Add hand-checkable tests for the highest-risk calculations.
4. Fix one critical bug at a time.
5. Run a real-data baseline with a manifest and trade ledger.
6. Lock rules and evaluate validation/holdout periods.
7. Paper trade before considering real capital.

## Official documentation
- Antigravity rules: https://antigravity.google/docs/rules/
- Antigravity skills: https://antigravity.google/docs/skills
- Gemini prompt design: https://ai.google.dev/gemini-api/docs/prompting-strategies
