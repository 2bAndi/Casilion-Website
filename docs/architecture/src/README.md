# Rebuilding the architecture document

`../index.html` is generated. Edit the sources here, then rebuild.

| File | Purpose |
|---|---|
| `index.template.html` | Text, tables and styling. Figures are placeholders: `{{FIG:<file>.svg}}`. |
| `gen_svgs.py` | Draws figures 1, 2, 7 and 10 (hand-laid-out SVG). |
| `../diagrams/*.mmd` | Mermaid sources of the other figures. |
| `mermaid-config.json` | Shared Mermaid theme. |
| `assemble.py` | Inlines all SVG figures into `../index.html`. |

## Steps

```sh
# 1. hand-laid-out figures (Python 3, no dependencies)
python gen_svgs.py

# 2. Mermaid figures (Node.js; mermaid-cli downloads its own browser on first use)
#    give each figure its own id: mm-03a, mm-03b, mm-04, mm-05, mm-06, mm-08, mm-09, mm-11
npx -p @mermaid-js/mermaid-cli mmdc -c mermaid-config.json -b white -I mm-04 \
    -i ../diagrams/04-purchase-license-sequence.mmd -o ../diagrams/04-purchase-license-sequence.svg

# 3. assemble the HTML
python assemble.py
```

The `-I` id matters: all figures are inlined into one page, and Mermaid scopes its styles by that id.
