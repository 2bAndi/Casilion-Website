# Internal knowledge (AI enclave only)

Nothing in this folder is ever shown to customers.

- `canaries.txt` - one marker string per line. Plant the same strings as comments in the private
  source tree. If one ever appears in a draft reply, the output guard blocks the reply and reports
  that the wall between analyst and responder has a hole.
- The private product repository itself is *not* stored here. Point `PRODUCT_REPO_DIR` at a
  read-only checkout on the machine that runs the AI worker.
