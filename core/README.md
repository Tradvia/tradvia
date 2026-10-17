# Tradvia Core

Community edition of Tradvia, licensed under
[FSL-1.1-ALv2](LICENSE.md).

Every source file in this directory starts with:

```
SPDX-License-Identifier: FSL-1.1-ALv2
Copyright (c) 2026 Anderson dos Santos Dutra
```

## Extension points

`core/` exposes interfaces (plugins, hooks, provider registries) that the
Enterprise Edition implements. Core code must work, build and pass its tests
with the `ee/` directory deleted. Never import from `ee/`.
