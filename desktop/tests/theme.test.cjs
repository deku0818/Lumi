const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { compile } = require('@tailwindcss/node')

test('theme colors do not override typography utilities', async () => {
  const base = path.join(__dirname, '../src')
  const compiler = await compile(fs.readFileSync(path.join(base, 'index.css'), 'utf8'), {
    base, onDependency() {},
  })
  const sizes = ['xs', 'sm', 'base', 'lg', 'xl', '2xl', '3xl', '4xl', '5xl', '6xl', '7xl', '8xl', '9xl']
  const checks = [
    ...sizes.map(size => [`text-${size}`, 'font-size']),
    ...['sans', 'mono', 'heading'].map(family => [`font-${family}`, 'font-family']),
    ...['normal', 'medium', 'semibold', 'bold'].map(weight => [`font-${weight}`, 'font-weight']),
    ['text-foreground', 'color'],
    ['text-muted-foreground', 'color'],
    ['text-popover-foreground', 'color'],
    ['text-primary', 'color'],
    ['bg-muted', 'background-color'],
    ['bg-accent', 'background-color'],
  ]
  const css = compiler.build(checks.map(([candidate]) => candidate))
  for (const [candidate, property] of checks) {
    const rule = css.match(new RegExp(`\\.${candidate}\\s*\\{([^}]+)\\}`))?.[1]
    assert.ok(rule, `${candidate} must generate a rule`)
    assert.match(rule, new RegExp(`(?:^|;)\\s*${property}:`), `${candidate} must set ${property}`)
    if (property.startsWith('font-')) {
      assert.doesNotMatch(rule, /(?:^|;)\s*color:/, `${candidate} must not set a color`)
    }
  }
})
