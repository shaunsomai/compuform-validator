# Computaform Extraction Validator

Static validation site for South African Computaform PDF extraction outputs.

Open `index.html` locally or publish this folder with GitHub Pages. The site reads `site_manifest.json`, then loads each meeting's `*_extraction.json` file from `outputs/<meeting>/`.

The source PDFs and local Python virtual environment are intentionally ignored by git.

## Reusable extraction template

Use `extract_card_template.py` for normal re-extractions. It runs the core extractor, applies the anomaly-handler template, writes the PDF-named output folder, and can refresh the website manifest.

```powershell
.\.venv\Scripts\python.exe .\extract_card_template.py --pdf ".\pdfs\HOLLYWOODBETS GREYVILLE@2026.07.12.pdf" --update-manifest
```

When a new PDF anomaly appears, add a named handler to `ANOMALY_HANDLERS` in `extract_card_template.py`. Keep handlers small and measurable: repair the field, add a validation note, and expose a count in `template_quality_checks`.
