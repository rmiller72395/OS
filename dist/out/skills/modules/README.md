# Skill Modules (manager upskilling)

Each module is a markdown file with YAML frontmatter:

- `module_name`: string
- `tools`: list of tool names this module documents
- `scopes`: list of scopes
- `verification_checklist`: optional string/list
- `common_failure_modes`: optional string/list

Only modules whose tools are in the current grant's allowed_tools are injected into worker prompts.
