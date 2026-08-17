"""Bounded, value-free DOM and open Shadow DOM contracts for repair evidence."""

from __future__ import annotations

from datetime import datetime


def capture_dom_shadow_contract(page, *, max_nodes: int = 300, max_depth: int = 10) -> dict:
    """Capture structural controls without input values, storage or page HTML."""
    captured_at = datetime.now().isoformat(timespec="seconds")
    try:
        payload = page.evaluate(
            r"""({maxNodes, maxDepth}) => {
                const nodes = [];
                let truncated = false;
                const allowed = ['role', 'id', 'name', 'data-cy', 'data-testid', 'aria-label'];
                const clean = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 160);
                const visible = element => {
                    try {
                        const style = getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' &&
                            Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
                    } catch (_) { return false; }
                };
                const family = (() => {
                    const path = location.pathname.toLowerCase();
                    if (path.includes('product_identity')) return 'product_identity';
                    if (path.includes('description')) return 'description';
                    if (path.includes('listing-approval') || path.includes('applications')) return 'listing_approval';
                    if (path.includes('add-product')) return 'add_product';
                    return 'unknown';
                })();
                const visit = (root, depth, parentIndex, hostChain) => {
                    if (!root || depth > maxDepth || nodes.length >= maxNodes) {
                        truncated = true;
                        return;
                    }
                    const children = root instanceof Document || root instanceof ShadowRoot
                        ? Array.from(root.children || root.querySelectorAll(':scope > *'))
                        : Array.from(root.children || []);
                    for (const element of children) {
                        if (nodes.length >= maxNodes) { truncated = true; break; }
                        const tag = String(element.tagName || '').toLowerCase();
                        if (!tag || ['script', 'style', 'template', 'noscript'].includes(tag)) continue;
                        const attrs = {};
                        for (const name of allowed) {
                            const value = element.getAttribute?.(name);
                            if (value) attrs[name.replace('data-', 'data_').replace('-', '_')] = clean(value);
                        }
                        let label = '';
                        try {
                            if (element.labels?.length) label = clean(element.labels[0].innerText || element.labels[0].textContent);
                        } catch (_) {}
                        const index = nodes.length;
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
                        });
                        visit(element, depth + 1, index, hostChain);
                        if (element.shadowRoot) {
                            const host = clean(element.id || element.getAttribute?.('data-testid') || tag);
                            visit(element.shadowRoot, depth + 1, index, [...hostChain, host]);
                        }
                    }
                };
                visit(document, 0, null, []);
                return {
                    schema_version: 1,
                    url_path: location.pathname,
                    page_family: family,
                    limits: {max_nodes: maxNodes, max_depth: maxDepth, max_string_chars: 160},
                    nodes,
                    truncated,
                };
            }""",
            {"maxNodes": int(max_nodes), "maxDepth": int(max_depth)},
        )
        if not isinstance(payload, dict):
            raise ValueError("dom_contract_not_object")
        payload["captured_at"] = captured_at
        return payload
    except Exception as exc:  # evidence capture must never break the business flow
        return {
            "schema_version": 1,
            "captured_at": captured_at,
            "page_family": "unknown",
            "nodes": [],
            "truncated": False,
            "capture_errors": [f"{type(exc).__name__}: {exc}"[:240]],
        }
