# Bulk conversion via a parallel workflow

For more than ~10 pages, convert with a multi-agent workflow (one agent per page)
instead of serially. A 51-page job ran in ~7 minutes this way. Requires explicit
user opt-in to multi-agent orchestration (it spends a lot of tokens — ~30K/page).

## Shape
1. Build the work-list: an array of tasks, each `{course, folder, notion_id,
   title, module, module_position, item_type, position}`. Derive `file =
   canvas-export/pages/<folder>/<notion_id>.html`.
2. One phase, `parallel()` over all tasks. Each agent gets: the conversion spec
   (`conversion-spec.md`, inlined into the prompt), the page's `notion_id`, the
   eyebrow/module/item context, and the absolute target path. It fetches, converts,
   writes the file, and returns `{ok, notes}`.
3. The orchestrator builds the manifests from its OWN task list (not agent
   returns), then you verify + push from the main loop (deterministic).

## Skeleton
```js
export const meta = { name: 'courseforge-bulk',
  description: 'Convert all Notion pages for <courses> into Canvas HTML',
  phases: [{ title: 'Convert' }] }

const ROOT = '<project>'
const SPEC = `<paste conversion-spec.md instructions here>`
const ALL = [ /* task objects */ ].map(t => ({ ...t,
  file: 'canvas-export/pages/' + t.folder + '/' + t.notion_id + '.html' }))

phase('Convert')
const results = await parallel(ALL.map(t => () =>
  agent(
    'Convert ONE Notion page.\n' +
    'FETCH: load the Notion fetch tool, fetch id "' + t.notion_id + '".\n' +
    'CONVERT per:\n' + SPEC + '\n' +
    'CONTEXT: eyebrow "' + eyebrow(t) + '", module "' + t.module + '", item "' + t.item_type + '".\n' +
    'WRITE to: ' + ROOT + '/' + t.file + '\n' +
    'RETURN {ok, notes}.',
    { label: t.title.slice(0,40), phase: 'Convert',
      schema: { type:'object', additionalProperties:false,
        properties:{ ok:{type:'boolean'}, notes:{type:'string'} }, required:['ok','notes'] } }
  ).then(r => ({ t, r })).catch(() => ({ t, r:{ ok:false, notes:'agent error' } }))
))
```

## CRITICAL follow-up
The fetch is unreliable for same-prefix IDs (see SKILL.md Gotcha 1). After the
workflow returns, **always run `Verify-Slots.ps1`**. Expect a few mismatches on a
large run; resolve them by reassembling from on-disk bodies before pushing. Do NOT
push straight from a bulk run without verifying.

## Iterating
The workflow tool persists the script to a file and returns its path. To re-run a
corrected subset, edit that file (swap the task array) and re-invoke with
`{scriptPath}`. Don't re-fetch through the flaky tool just to fix framing —
reassemble locally.
