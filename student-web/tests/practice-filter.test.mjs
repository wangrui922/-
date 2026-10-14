import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = await readFile(
  new URL('../src/components/practice/PracticeFilterCard.vue', import.meta.url),
  'utf8',
)
const segmentSource = await readFile(
  new URL('../src/stores/segment.ts', import.meta.url),
  'utf8',
)

test('practice filter temporarily hides section selection', () => {
  assert.equal(source.includes('练哪一块'), false)
  assert.equal(source.includes('SECTIONS'), false)
  assert.equal(source.includes('params.section'), false)
})

test('practice filter temporarily hides edition selection', () => {
  assert.equal(source.includes('>教材版本<'), false)
  assert.equal(source.includes('import { EDITIONS'), false)
  assert.equal(source.includes('params.edition'), false)
})

test('segment options cover primary, junior and senior high school', () => {
  assert.equal(segmentSource.includes("'高中'"), true)
  assert.equal(segmentSource.includes('[10, 12]'), true)
})
