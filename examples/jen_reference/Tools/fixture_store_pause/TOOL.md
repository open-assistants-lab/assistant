---
name: fixture_store_pause
description: Propose a revision-checked change to a synthetic store; explicit approval required.
command: >
  python {{tool_dir}}/../../fixture.py --state {{tool_dir}}/../../.fixture/state.sqlite3 pause --store-id {{store_id}} --paused {{paused}} --expected-revision {{expected_revision}}
parameters:
  type: object
  properties:
    store_id: {type: string}
    paused: {type: string, enum: ['true', 'false']}
    expected_revision: {type: integer, minimum: 1}
  required: [store_id, paused, expected_revision]
annotations:
  pipefail: true
  read_only: false
  requires_approval: true
---
Pending is not executed. A stale revision refuses mutation. No production access.
