import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { beforeEach, describe, expect, it } from 'vitest'

type Collector = (limits: { maxNodes: number; maxDepth: number }) => {
  schema_version: number
  url_path: string
  page_family: string
  nodes: Array<Record<string, unknown>>
  truncated: boolean
}

const source = readFileSync(resolve(process.cwd(), '../src/capture/dom_contract.js'), 'utf8')
const collect = new Function(`return (${source})`)() as Collector

beforeEach(() => {
  document.documentElement.innerHTML = '<head></head><body></body>'
  window.history.replaceState({}, '', '/product_identity/start?account=secret#fragment')
  Object.defineProperty(Element.prototype, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ width: 100, height: 20, top: 0, left: 0, right: 100, bottom: 20 }),
  })
})

describe('production DOM/Shadow DOM contract collector', () => {
  it('walks the main DOM and open shadow roots without values or hidden text', () => {
    const input = document.createElement('input')
    input.id = 'seller-input'
    input.value = 'never-record-this-value'
    input.setAttribute('aria-label', 'Seller identifier')
    document.body.append(input)

    const host = document.createElement('section')
    host.id = 'apply-host'
    const openRoot = host.attachShadow({ mode: 'open' })
    openRoot.innerHTML = '<button data-testid="apply-to-sell">Hidden body secret</button>'
    document.body.append(host)

    const closedHost = document.createElement('section')
    closedHost.id = 'closed-host'
    const closedRoot = closedHost.attachShadow({ mode: 'closed' })
    closedRoot.innerHTML = '<button id="closed-secret">Do not read</button>'
    document.body.append(closedHost)

    const result = collect({ maxNodes: 50, maxDepth: 8 })
    const serialized = JSON.stringify(result)
    const shadowButton = result.nodes.find(
      (node) => (node.attrs as Record<string, string> | undefined)?.data_testid === 'apply-to-sell',
    )

    expect(result.schema_version).toBe(2)
    expect(result.url_path).toBe('/product_identity/start')
    expect(result.page_family).toBe('product_identity')
    expect(shadowButton?.shadow_host_chain).toEqual(['apply-host'])
    expect(serialized).not.toContain('never-record-this-value')
    expect(serialized).not.toContain('Hidden body secret')
    expect(serialized).not.toContain('closed-secret')
    expect(serialized).not.toContain('account=secret')
  })

  it('enforces node and depth limits and reports truncation', () => {
    document.body.innerHTML = '<main><section><div><button id="deep">Apply</button></div></section></main>'
    const depthLimited = collect({ maxNodes: 50, maxDepth: 1 })
    const nodeLimited = collect({ maxNodes: 2, maxDepth: 20 })

    expect(depthLimited.truncated).toBe(true)
    expect(JSON.stringify(depthLimited)).not.toContain('deep')
    expect(nodeLimited.truncated).toBe(true)
    expect(nodeLimited.nodes).toHaveLength(2)
  })
})
