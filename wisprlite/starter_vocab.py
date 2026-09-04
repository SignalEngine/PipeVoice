"""Dev terms every speech engine gets wrong, shipped so they work on install.

`cfg.vocabulary` and `cfg.replacements` have existed for a long time and both
ship EMPTY, so the feature only ever worked for someone who found the settings
and typed a list by hand. Meanwhile "CLAUDE.md" comes back as "claw dot MD" or
"clawed MD" depending on which way the model guessed that run - James watched it
land correctly once and wrong the next time in the same conversation.

Two mechanisms, deliberately:
  TERMS      bias the recogniser BEFORE it decides (engine prompt / keywords).
             Cheap, but a hint - the engine can still ignore it.
  FIXES      rewrite AFTER recognition. Deterministic, and the only thing that
             can guarantee a spelling. Used for the handful that are reliably
             mangled the same way.

Scope is deliberately narrow: developer tooling, because that is what PipeVoice
is dictated into (terminals, editors, agent prompts). It is not a dictionary and
must not grow into one - every term here costs prompt budget on every single
utterance, and a term that is merely uncommon does not belong.
"""

from __future__ import annotations

# Bias hints. Proper spelling and casing matter: this string IS what the engine
# is shown, so "Github" here teaches the wrong capitalisation.
TERMS = [
    # agent tooling - the vocabulary of the thing people dictate INTO
    "CLAUDE.md", "Claude Code", "Cursor", "Copilot", "OpenAI", "Anthropic",
    "LLM", "prompt", "token", "repo", "PR", "diff", "commit", "rebase",
    # everyday shell + language terms that come back as ordinary words
    "npm", "npx", "pnpm", "yarn", "git", "GitHub", "CLI", "stdout", "stderr",
    "env", "regex", "JSON", "YAML", "API", "SDK", "UUID", "SQL", "CSS", "HTML",
    "async", "await", "boolean", "int", "str", "bool", "nullable", "enum",
    "TypeScript", "JavaScript", "Python", "Rust", "Go", "React", "Next.js",
    "Node", "Docker", "kubectl", "Kubernetes", "PostgreSQL", "SQLite", "Redis",
    "VS Code", "PowerShell", "SSH", "localhost", "webhook", "endpoint",
]

# Post-recognition rewrites. ONLY for terms that are reliably mangled the same
# way - a wrong fix is worse than no fix, because it silently corrupts text the
# engine actually got right. Keys are matched case-insensitively downstream.
FIXES = {
    "claude dot md": "CLAUDE.md",
    "claw dot md": "CLAUDE.md",
    "clawed dot md": "CLAUDE.md",
    "claude md": "CLAUDE.md",
    "readme dot md": "README.md",
    "package dot json": "package.json",
    "dot env": ".env",
    "en pee em": "npm",
    "get hub": "GitHub",
    "git hub": "GitHub",
    "pee are": "PR",
    "sequel": "SQL",
    "post gres": "PostgreSQL",
    "kube control": "kubectl",
    "kube cuttle": "kubectl",
    "vee ess code": "VS Code",
    "jason": "JSON",
    "yamel": "YAML",
    "reg ex": "regex",
    "standard out": "stdout",
    "standard error": "stderr",
    "local host": "localhost",
}


def terms_string() -> str:
    """The engine-bias list, as the comma-separated string cfg.vocabulary holds."""
    return ", ".join(TERMS)


def merge_into(vocabulary: str, replacements: dict) -> tuple[str, dict]:
    """Add the starter set WITHOUT touching anything the user already set.

    The user always wins: a term they already have is not duplicated, and a fix
    they wrote for the same key is never overwritten. Merging rather than
    replacing is what makes this safe to apply to an existing install.
    """
    existing = [t.strip() for t in (vocabulary or "").split(",") if t.strip()]
    lowered = {t.lower() for t in existing}
    merged = existing + [t for t in TERMS if t.lower() not in lowered]

    fixes = dict(replacements or {})
    user_keys = {k.lower() for k in fixes}
    for wrong, right in FIXES.items():
        if wrong.lower() not in user_keys:
            fixes[wrong] = right
    return ", ".join(merged), fixes
