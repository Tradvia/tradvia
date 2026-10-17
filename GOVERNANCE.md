# Governance

Tradvia is developed in the open and stewarded by Anderson dos Santos Dutra (the
"Company"), which owns the trademark and decides the roadmap.

## Roles

| Role | Who | Can |
|---|---|---|
| Contributor | Anyone with a signed CLA | Open issues and pull requests |
| Maintainer | Listed in `.github/CODEOWNERS` for `core/` | Review and merge `core/` changes |
| EE maintainer | Company employees in `@Tradvia/ee-maintainers` | Review and merge `ee/` changes |
| Admin | Company owners of the GitHub organization | Change repository settings and rulesets |

## Becoming a maintainer

A contributor with a sustained record of quality contributions can be
nominated by any maintainer. Existing maintainers decide by lazy consensus
over 7 days.

## Decisions

- Day-to-day changes: pull request review.
- Larger changes (architecture, new extension points, licensing): a written
  proposal in `docs/adr/` (Architecture Decision Record) open for comment for
  at least 7 days.
- Final say on roadmap, licensing and trademark: the Company.

## License changes

Released versions keep the license they shipped with. Each `core/` release
converts to Apache 2.0 two years after publication, as written in the FSL.
