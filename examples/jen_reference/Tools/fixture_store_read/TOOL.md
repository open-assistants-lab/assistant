---
name: fixture_store_read
description: Read a synthetic store's current pause state and revision.
command: >
  python {{tool_dir}}/../../fixture.py --state {{tool_dir}}/../../.fixture/state.sqlite3 read --store-id {{store_id}}
parameters:
  type: object
  properties:
    store_id: {type: string}
  required: [store_id]
annotations:
  read_only: true
---
Only fixture-alpha and fixture-beta are valid targets. No network access.
