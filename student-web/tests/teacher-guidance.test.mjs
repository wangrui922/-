import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = await readFile(
  new URL('../src/views/AiTeacher.vue', import.meta.url),
  'utf8',
)

test('exercise guide handlers reject a second click before adding a duplicate student bubble', () => {
  assert.match(
    source,
    /function handleGuideSend\(work: string\): void \{[\s\S]*?if \(!thread \|\| guideLoading\.value\) return/,
  )
  assert.match(
    source,
    /function handleGuideQuick\(action: QuickAction, draft: string\): void \{[\s\S]*?if \(!thread \|\| guideLoading\.value\) return/,
  )
})
