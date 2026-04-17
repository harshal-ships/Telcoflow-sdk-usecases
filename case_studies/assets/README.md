# Case Study Diagram Assets

This folder holds the source and rendered images for every diagram in the B3Networks Telcoflow SDK case study pack.

## Folder Structure

```
assets/
├── portfolio_overview.mmd / .png        # Master portfolio diagram (overview doc)
├── after_hours_voicemail/               # One folder per case study
│   ├── solution_overview.mmd / .png
│   └── call_flow.mmd / .png
├── appointment_booking/
│   ├── solution_overview.mmd / .png
│   └── call_flow.mmd / .png
├── call_monitoring/
│   ├── solution_overview.mmd / .png
│   └── call_flow.mmd / .png
├── escalation_agent/
│   ├── solution_overview.mmd / .png
│   └── call_flow.mmd / .png
├── interactive_notifications/
│   ├── solution_overview.mmd / .png
│   └── call_flow.mmd / .png
├── receptionist_agent/
│   ├── solution_overview.mmd / .png
│   └── call_flow.mmd / .png
├── screening_agent/
│   ├── solution_overview.mmd / .png
│   └── call_flow.mmd / .png
└── smart_ivr/
    ├── solution_overview.mmd / .png
    └── call_flow.mmd / .png
```

Each case study has two diagrams:

- **solution_overview** — horizontal architecture view showing how the caller, the Telcoflow SDK, the assistant, and supporting systems connect.
- **call_flow** — top-down step-by-step view of the call journey, including decision branches.

## File Types

- `.mmd` — Mermaid source code. Version-controlled, human-readable, easy to edit.
- `.png` — Rendered image. Drop directly into Google Docs, Google Slides, PowerPoint, Notion, Keynote, email, etc.

## Using the PNGs

**Google Docs / Slides**

1. `Insert` → `Image` → `Upload from computer`
2. Select the relevant `.png`
3. Resize to fit your layout

**Fern / GitHub / Notion**

Markdown viewers already render the Mermaid blocks inside the `.md` case studies natively. The PNGs are for environments that do not support Mermaid.

## Re-rendering Diagrams

If you edit a `.mmd` file, regenerate its PNG by running:

```bash
# From repo root
python3 case_studies/assets/render_diagrams.py           # render only missing PNGs
python3 case_studies/assets/render_diagrams.py --force   # re-render everything
```

The script uses the public [mermaid.ink](https://mermaid.ink) renderer, so it requires internet access but no local dependencies beyond Python 3.

## Editing Diagrams

1. Open the relevant `.mmd` file in any editor.
2. Mermaid syntax reference: https://mermaid.js.org/syntax/flowchart.html
3. Live preview while editing: https://mermaid.live
4. Save the `.mmd` file, then re-run the render script to refresh the `.png`.
5. Remember to also update the matching Mermaid block inside the case study `.md` file so the rendered documentation stays in sync.
