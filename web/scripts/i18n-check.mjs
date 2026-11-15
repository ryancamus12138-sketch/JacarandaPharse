// Interface translation check (ADR 0065).
//
// 1. Every key passed to t() / msg() / i18n.t() has an English translation in src/i18n/en.json.
// 2. en.json has no unused keys (plural forms key_one / key_other count as uses of key).
// 3. No Chinese text appears outside t() / msg(), except on lines marked `i18n-ignore`
//    (or the line after `i18n-ignore-next-line`), console calls and localeCompare arguments.
//
// Usage: node scripts/i18n-check.mjs [--list-missing]
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseAst } from 'vite'

const root = fileURLToPath(new URL('..', import.meta.url))
const src = join(root, 'src')
const CJK = /[　-〿一-鿿＀-￯]/
const TRANSLATORS = new Set(['t', 'msg'])

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return files(path)
    return /\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts') ? [path] : []
  })
}

function isTranslatorCall(node) {
  if (node?.type !== 'CallExpression') return false
  const callee = node.callee
  if (callee.type === 'Identifier') return TRANSLATORS.has(callee.name)
  return callee.type === 'MemberExpression' && !callee.computed && callee.property.name === 't'
}

function isExempt(node) {
  if (node?.type !== 'CallExpression' || node.callee.type !== 'MemberExpression') return false
  const object = node.callee.object
  return (object.type === 'Identifier' && object.name === 'console') || node.callee.property.name === 'localeCompare'
}

/** String keys of a translator argument: literals, or both branches of a conditional. */
function literalKeys(arg) {
  if (!arg) return []
  if (arg.type === 'Literal' && typeof arg.value === 'string') return [arg.value]
  if (arg.type === 'TemplateLiteral' && arg.expressions.length === 0) return [arg.quasis[0].value.cooked]
  if (arg.type === 'ConditionalExpression') return [...literalKeys(arg.consequent), ...literalKeys(arg.alternate)]
  return []
}

const used = new Map()
const untranslated = []
const dynamic = []

for (const file of files(src)) {
  if (file.includes(`${join('src', 'i18n')}`)) continue
  const code = readFileSync(file, 'utf8')
  const lines = code.split('\n')
  const lang = file.endsWith('.tsx') ? 'tsx' : 'ts'
  const ast = parseAst(code, { lang })
  const lineOf = (offset) => code.slice(0, offset).split('\n').length
  const ignored = (line) =>
    lines[line - 1]?.includes('i18n-ignore') || lines[line - 2]?.includes('i18n-ignore-next-line')

  const visit = (node, inTranslator, exempt) => {
    if (!node || typeof node.type !== 'string') return
    if (isTranslatorCall(node)) {
      const keys = literalKeys(node.arguments[0])
      if (!keys.length && node.arguments[0]) dynamic.push(`${relative(root, file)}:${lineOf(node.start)}`)
      for (const key of keys) {
        if (!used.has(key)) used.set(key, `${relative(root, file)}:${lineOf(node.start)}`)
      }
      node.arguments.forEach((a, i) => visit(a, i === 0, exempt))
      return
    }
    const nowExempt = exempt || isExempt(node)
    let text = null
    if (node.type === 'Literal' && typeof node.value === 'string') text = node.value
    else if (node.type === 'JSXText') text = node.value
    else if (node.type === 'TemplateElement') text = node.value.cooked
    if (text && CJK.test(text) && !inTranslator && !nowExempt && !ignored(lineOf(node.start))) {
      untranslated.push(`${relative(root, file)}:${lineOf(node.start)}: ${text.trim().slice(0, 60)}`)
    }
    const passOn = inTranslator && node.type === 'ConditionalExpression'
    for (const [key, value] of Object.entries(node)) {
      if (key === 'parent') continue
      const child = passOn && (key === 'consequent' || key === 'alternate')
      if (Array.isArray(value)) value.forEach((v) => visit(v, false, nowExempt))
      else if (value && typeof value === 'object') visit(value, child, nowExempt)
    }
  }
  visit(ast, false, false)
}

const en = JSON.parse(readFileSync(join(src, 'i18n', 'en.json'), 'utf8'))
const base = (key) => key.replace(/_(zero|one|two|few|many|other)$/, '')
const missing = [...used.keys()].filter((k) => !(k in en) && !(`${k}_one` in en) && !(`${k}_other` in en))
const unused = Object.keys(en).filter((k) => !used.has(k) && !used.has(base(k)))
const empty = Object.entries(en).filter(([, v]) => typeof v !== 'string' || !v.trim()).map(([k]) => k)

if (process.argv.includes('--list-missing')) {
  console.log(JSON.stringify(missing, null, 1))
  process.exit(0)
}

let failed = false
const report = (title, list) => {
  if (!list.length) return
  failed = true
  console.error(`\n${title} (${list.length}):`)
  for (const item of list.slice(0, 50)) console.error(`  ${item}`)
  if (list.length > 50) console.error(`  … ${list.length - 50} more`)
}
report('Chinese text outside t()/msg()', untranslated)
report('Keys without an English translation', missing.map((k) => `${used.get(k)}: ${k}`))
report('Unused keys in en.json', unused)
report('Empty translations in en.json', empty)
console.log(`${used.size} keys used, ${Object.keys(en).length} English entries, ${dynamic.length} dynamic t() calls.`)
process.exit(failed ? 1 : 0)
