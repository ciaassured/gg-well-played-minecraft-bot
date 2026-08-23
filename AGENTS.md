# Agent guidance

- Preserve top-level project boundaries: each subproject must remain independently buildable, configurable, and testable. Do not make one subproject inspect, import, configure, or derive behavior from another; coordinate only through explicit contracts or externally supplied configuration.
- Use the root `README.md` as the canonical guide for orchestrating the project, including its Nix commands; do not duplicate those instructions here.
- Follow [Scoped Commits](https://scopedcommits.com/): format normal commit messages as `<scope>: <description>`, using the affected subproject or repository area as the scope (for example, `trainer: add evaluation metric`).
