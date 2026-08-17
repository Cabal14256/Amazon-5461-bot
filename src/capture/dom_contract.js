({ maxNodes, maxDepth }) => {
  const nodes = []
  let truncated = false
  const allowed = ['role', 'id', 'name', 'data-cy', 'data-testid', 'aria-label']
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 160)
  const visible = (element) => {
    try {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' &&
        Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0
    } catch (_) {
      return false
    }
  }
  const family = (() => {
    const path = location.pathname.toLowerCase()
    if (path.includes('product_identity')) return 'product_identity'
    if (path.includes('description')) return 'description'
    if (path.includes('listing-approval') || path.includes('applications')) return 'listing_approval'
    if (path.includes('add-product')) return 'add_product'
    return 'unknown'
  })()
  const visit = (root, depth, parentIndex, hostChain) => {
    if (!root || depth > maxDepth || nodes.length >= maxNodes) {
      truncated = true
      return
    }
    const children = root instanceof Document || root instanceof ShadowRoot
      ? Array.from(root.children || root.querySelectorAll(':scope > *'))
      : Array.from(root.children || [])
    for (const element of children) {
      if (nodes.length >= maxNodes) {
        truncated = true
        break
      }
      const tag = String(element.tagName || '').toLowerCase()
      if (!tag || ['script', 'style', 'template', 'noscript'].includes(tag)) continue
      const attrs = {}
      for (const name of allowed) {
        const value = element.getAttribute?.(name)
        if (value) attrs[name.replace('data-', 'data_').replace('-', '_')] = clean(value)
      }
      let label = ''
      try {
        if (element.labels?.length) {
          label = clean(element.labels[0].innerText || element.labels[0].textContent)
        }
      } catch (_) {}
      const index = nodes.length
      nodes.push({
        tag,
        attrs,
        label,
        visible: visible(element),
        disabled: Boolean(element.disabled || element.getAttribute?.('aria-disabled') === 'true'),
        parent_index: parentIndex,
        shadow_host_chain: hostChain.slice(-5),
        child_count: Number(element.children?.length || 0),
        has_open_shadow_root: Boolean(element.shadowRoot),
      })
      visit(element, depth + 1, index, hostChain)
      if (element.shadowRoot) {
        const host = clean(element.id || element.getAttribute?.('data-testid') || tag)
        visit(element.shadowRoot, depth + 1, index, [...hostChain, host])
      }
    }
  }
  visit(document, 0, null, [])
  return {
    schema_version: 2,
    url_path: location.pathname,
    page_family: family,
    limits: { max_nodes: maxNodes, max_depth: maxDepth, max_string_chars: 160 },
    nodes,
    truncated,
  }
}
