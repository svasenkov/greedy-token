# Cut checklist — greedy-token v0.16.2

**Status:** READY FOR RELEASE. Create the tag and GitHub release only after the
exact commit passes the mandatory `required matrix gate`.

Patch after v0.16.1: hub picker offered `since=all` but `parse_since` rejected
it; the crystals table mixed pytest fixtures, rejected lifecycle, and workshop
prompts with workspace candidates. Also Ollama Basic auth, MCP icon size skip,
and workspace google-sheets live + `delete-sheet`.

## Summary

- **`since=all`:** `parse_since` accepts `all` / `lifetime` / `total` (no lower
  bound). Hub API, CLI `report --since all`, and MCP usage share that parser.
- **Hub crystals:** hide reject / pytest-fixture / stale inbox; workshop tails
  (`greedy-guru-lesson`, `greedy-token-workshop`, id+email stems) go to a Lesson
  list, not the workspace table.
- **Ollama:** Basic auth + require the configured model in `/api/tags`; tag usage
  events.
- **MCP:** skip oversized PNG icons on FastMCP initialize (Cursor offerings timeout).
- **Routes:** `python-google-sheets` live (`delete-sheet` for `zds-sheets-*` only);
  retire unused shadows (`provider-balance`, missing students-sheet).
- **Other:** overlay contract path; Allure Palette A `ui` pyramid layer; README
  badge Go 1.27.

## Honest evidence

The local macOS/Python 3.12 release gate on 2026-09-08 passed:

- coverage run: 1209 passed, 4 skipped; 100% branch coverage across 7573 statements and 2578 branches;
- explicit `0.16.2` release-version gate;
- Allure `minTestsCount` synced to 1213 (pytest collected).

## Contract evidence

| Contract | Evidence |
| --- | --- |
| Version `0.16.2` | `pyproject.toml`, `tests/test_evidence_benchmark.py` |
| Unbounded since | `UNBOUNDED_SINCE` in `src/greedy_token/usage.py`; hub `#/crystals?since=all` |
| Lesson vs workspace | `crystal_contour` in `src/greedy_token/hub/crystallize.py` |

## Out of scope

- Retagging `v0.16.1`
- Hub `#/ladder` progress view
- PyPI teach-pack / lesson D
- greedy.guru mill IR

## Release gates

1. Run `./scripts/release-gate.sh 0.16.2` in a clean environment.
2. Push the cut commit to `main` (`git push origin release/0.16.1:main`).
3. Require the exact commit's `Test / required matrix gate` to succeed.
4. Annotated tag `v0.16.2` on that commit only.
5. `gh release create v0.16.2` → Publish to PyPI.

## Tag / publish

```bash
git tag -a v0.16.2 -m "Release v0.16.2: hub since=all and lesson crystals split"
git push origin v0.16.2
gh release create v0.16.2 --title "v0.16.2 — Hub since=all and crystals split" --notes-file CUT-v0.16.2.md
```
