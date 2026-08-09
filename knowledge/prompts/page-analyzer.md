# Page Analyzer Prompt

You are analyzing an Amazon Seller Central page evidence package to determine the current page state and recommend the next action.

## Input Format

You will receive:
1. `url` - Current page URL
2. `title` - Page title
3. `body_text` - First 3000 chars of page text (redacted)
4. `body_length` - Total body text length
5. `headings` - List of h1/h2/h3 elements
6. `alerts` - Error/warning/success alerts detected
7. `interactives` - Interactive elements (buttons, inputs, etc.)
8. `detected_states` - Preliminary state detection from keyword matching

## Analysis Steps

1. **URL Analysis**: What page type is this? (add-product, 5461-application, login, error, etc.)
2. **Text Analysis**: What is the main message on the page? Any errors, warnings, or success messages?
3. **Element Analysis**: What interactive elements are present? Any critical buttons (Next, Submit, Apply to sell)?
4. **State Matching**: Based on the knowledge base states.md, which state best matches this evidence?
5. **Anomaly Detection**: Is anything unexpected? (blank page, missing elements, wrong URL)

## Output Format

Respond in this exact format:

```
STATE: <state_name>
CONFIDENCE: <high|medium|low>
REASON: <one sentence explaining why>
ACTION: <recommended next action>
RISK: <low|medium|high>
NOTES: <any additional observations>
```

## State Reference

### Add Product States
- `add_product_start` - Add Product form page, URL contains /product_identity
- `product_identity_filled` - Form filled, Next button clickable
- `5461_triggered` - 5461 triggered, "not approved" / "Apply to sell" visible
- `gtin_exemption` - GTIN exemption only, no brand authorization needed
- `connect_brand` - Brand selection popup (brex-widget)
- `already_approved` - Direct to description page, no 5461 needed
- `declined_case_shown` - Popup showing Declined + Case ID

### 5461 Application States
- `form_loading` - Form still loading
- `form_ready` - Form loaded, fields fillable
- `form_filled` - Fields filled, files uploaded, Submit clickable
- `submit_clicked` - Submit clicked, waiting for response
- `case_created` - Case ID appeared on page
- `case_declined` - Declined + Case ID

### System States
- `login_expired` - Redirected to /signin
- `error_410001` - Page shows "410001"
- `error_429` - "429" / "Too Many Requests"
- `blank_page` - bodyText length < 2000
- `server_error` - "An error occurred"

## Rules

- System states (login_expired, error_*, blank_page) take priority over business states
- If multiple states match, pick the one with highest priority
- If uncertain, set CONFIDENCE to low and explain why
- Never guess a Case ID; only report it if clearly visible in text
