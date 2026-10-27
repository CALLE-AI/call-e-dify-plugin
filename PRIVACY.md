# Privacy Policy

This plugin sends configured phone-call task data to the CALL-E API endpoint selected by the Dify workspace administrator.

## Data Sent To CALL-E

Depending on tool inputs, the plugin may send:

- destination phone number
- call task instructions
- region and locale hints
- metadata supplied by the workflow
- result schema fields

The plugin does not intentionally send Dify API keys, Dify workspace secrets, or CALL-E provider credentials in tool outputs.

## Data Returned

CALL-E may return call status, recipient status, attempt status, transcript turns, summaries, structured results, evidence, metadata, and raw call result fields.

The plugin masks destination phone fields and phone fields in metadata and error details before returning data to Dify. Call business results retain their original values and types, including phone-named fields, in `result`, `structured_result`, and `raw_call_result.result`. Results, summaries, and transcript text may contain personal data and are not anonymized.

## Storage

The plugin does not persist data outside the Dify plugin runtime. Dify and CALL-E may retain data according to their own workspace and service configuration.

## User Responsibility

Only call numbers that you own or are explicitly authorized to call. Do not include secrets, unnecessary personal data, medical records, payment details, or private credentials in call tasks or metadata.
